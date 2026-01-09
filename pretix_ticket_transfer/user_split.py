import json
import logging
from decimal import Decimal
from django.db import transaction
from django.utils.timezone import now

from pretix.base.signals import order_split, order_changed
from pretix.base.secrets import assign_ticket_secret
from pretix.base.models.orders import Order, OrderPosition, OrderFee, OrderRefund, OrderPayment, generate_secret
from pretix.base.services.orders import OrderChangeManager, OrderError, error_messages
from pretix.base.models.tax import TaxRule
from pretix.base.i18n import language
from pretix.base.email import get_email_context
from django.utils.translation import gettext as _

from i18nfield.strings import LazyI18nString
from pretix.base.services.mail import SendMailException
from pretix.helpers import OF_SELF
from pretix.helpers.models import modelcopy

from .utils import transfer_needs_accept

logger = logging.getLogger(__name__)

TICKET_TRANSFER_START = 1
TICKET_TRANSFER_DONE = 2
TICKET_TRANSFER_SENT = 23
TICKET_TRANSFER_PENDING_PAYMENT = 3  # Transfer initiated, waiting for new owner to pay
TICKET_TRANSFER_COMPLETED = 4  # Transfer completed, old owner refunded

class TicketTransferChangeManager(OrderChangeManager):
    """
    dont complete_cancel check
    no notify
    """
    def commit(self, check_quotas=True):
        if self._committed:
            # an order change can only be committed once
            raise OrderError(error_messages['internal'])
        self._committed = True

        if not self._operations:
            # Do nothing
            return

        # Clear prefetched objects cache of order. We're going to modify the positions and fees and we have no guarantee
        # that every operation tuple points to a position/fee instance that has been fetched from the same object cache,
        # so it's dangerous to keep the cache around.
        self.order._prefetched_objects_cache = {}

        self._check_order_size()

        with transaction.atomic():
            locked_instance = Order.objects.select_for_update(of=OF_SELF).get(pk=self.order.pk)
            if locked_instance.last_modified != self.order.last_modified:
                raise OrderError(error_messages['race_condition'])

            original_total = self.order.total
            if self.order.status in (Order.STATUS_PENDING, Order.STATUS_PAID):
                if check_quotas:
                    self._check_quotas()
                self._check_seats()
            self._create_locks()
            #self._check_complete_cancel()
            self._check_and_lock_memberships()
            try:
                self._perform_operations()
            except TaxRule.SaleNotAllowed:
                raise OrderError(self.error_messages['tax_rule_country_blocked'])
            new_total = self._recalculate_rounding_total_and_payment_fee()
            totaldiff = new_total - original_total
            self._check_paid_price_change(totaldiff)
            self._check_paid_to_free(totaldiff)
            if self.order.status in (Order.STATUS_PENDING, Order.STATUS_PAID):
                self._reissue_invoice()
            self._clear_tickets_cache()
            self.order.touch()
            self.order.create_transactions()
            if self.split_order:
                self.split_order.create_transactions()

        #if self.notify:
        #    notify_user_changed_order(
        #        self.order, self.user, self.auth,
        #        self._invoices if self.event.settings.invoice_email_attachment else []
        #    )
        #    if self.split_order:
        #        notify_user_changed_order(
        #            self.split_order, self.user, self.auth,
        #            list(self.split_order.invoices.all()) if self.event.settings.invoice_email_attachment else []
        #        )

        order_changed.send(self.order.event, order=self.order)

    """
    no invoice copy
    clear answers
    """
    def _create_split_order(self, split_positions):
        split_order = Order.objects.get(pk=self.order.pk)
        split_order.pk = None
        split_order.code = None
        split_order.datetime = now()
        split_order.secret = generate_secret()
        split_order.require_approval = self.order.require_approval and any(p.requires_approval(invoice_address=self._invoice_address) for p in split_positions)
        split_order.save()
        split_order.log_action('pretix_ticket_transfer.changed.split_from', user=self.user, auth=self.auth, data={
            'original_order': self.order.code
        })

        for op in split_positions:
            self.order.log_action('pretix_ticket_transfer.changed.split', user=self.user, auth=self.auth, data={
                'position': op.pk,
                'positionid': op.positionid,
                'old_item': op.item.pk,
                'old_variation': op.variation.pk if op.variation else None,
                'old_price': op.price,
                'new_order': split_order.code,
            })
            op.order = split_order
            assign_ticket_secret(
                self.event, position=op, force_invalidate=True,
            )
            op.save()

        ## clear answers
            op.answers.clear()

        #try:
        #    ia = modelcopy(self.order.invoice_address)
        #    ia.pk = None
        #    ia.order = split_order
        #    ia.save()
        #except InvoiceAddress.DoesNotExist:
        #    pass

        split_order.total = sum([p.price for p in split_positions if not p.canceled])

        for fee in self.order.fees.exclude(fee_type=OrderFee.FEE_TYPE_PAYMENT):
            new_fee = modelcopy(fee)
            new_fee.pk = None
            new_fee.order = split_order
            split_order.total += new_fee.value
            new_fee.save()

        if split_order.total != Decimal('0.00') and self.order.status != Order.STATUS_PAID:
            pp = self._get_payment_provider()
            if pp:
                payment_fee = pp.calculate_fee(split_order.total)
            else:
                payment_fee = Decimal('0.00')
            fee = split_order.fees.get_or_create(fee_type=OrderFee.FEE_TYPE_PAYMENT, defaults={'value': 0})[0]
            fee.value = payment_fee
            fee._calculate_tax()
            if payment_fee != 0:
                fee.save()
            elif fee.pk:
                fee.delete()
            split_order.total += fee.value

        remaining_total = sum([p.price for p in self.order.positions.all()]) + sum([f.value for f in self.order.fees.all()])
        offset_amount = min(max(0, self.completed_payment_sum - remaining_total), split_order.total)
        if offset_amount >= split_order.total:
            split_order.status = Order.STATUS_PAID
        else:
            split_order.status = Order.STATUS_PENDING
            #if self.order.status == Order.STATUS_PAID:
            #    split_order.set_expires(
            #        now(),
            #        list(set(p.subevent_id for p in split_positions))
            #    )
        split_order.save()

        if offset_amount > Decimal('0.00'):
            split_order.payments.create(
                state=OrderPayment.PAYMENT_STATE_CONFIRMED,
                amount=offset_amount,
                payment_date=now(),
                provider='offsetting',
                info=json.dumps({'orders': [self.order.code]})
            )
            self.order.refunds.create(
                state=OrderRefund.REFUND_STATE_DONE,
                amount=offset_amount,
                execution_date=now(),
                provider='offsetting',
                info=json.dumps({'orders': [split_order.code]})
            )

        #if split_order.total != Decimal('0.00') and self.order.invoices.filter(is_cancellation=False).last():
        #    generate_invoice(split_order)

        order_split.send(sender=self.order.event, original=self.order, split_order=split_order)
        return split_order

def notify_user_split_order_source(order, user=None, auth=None, invoices=[]):
    with language(order.locale, order.event.settings.region):
        email_template = order.event.settings.get('pretix_ticket_transfer_sender_mailtext', as_type=LazyI18nString)
        email_subject = str(order.event.settings.get('pretix_ticket_transfer_sender_subject', as_type=LazyI18nString)).format(code=order.code)
        email_context = get_email_context(event=order.event, order=order)
        try:
          order.send_mail(
            email_subject, email_template, email_context,
            'pretix.event.order.email.ticket_transfer_sender', user, auth=auth, invoices=invoices, attach_tickets=True)
        except SendMailException:
          logger.exception('Tickettransfer sender email could not be sent')

def notify_user_split_order_target(order, user=None, auth=None, invoices=[]):
    with language(order.locale, order.event.settings.region):
        email_template = order.event.settings.get('pretix_ticket_transfer_recipient_mailtext', as_type=LazyI18nString)
        email_subject = str(order.event.settings.get('pretix_ticket_transfer_recipient_subject', as_type=LazyI18nString)).format(code=order.code)
        email_context = get_email_context(event=order.event, order=order)
        try:
          order.send_mail(
            email_subject, email_template, email_context,
            'pretix.event.order.email.ticket_transfer_recipient', user, auth=auth, invoices=invoices, attach_tickets=True)
        except SendMailException:
            logger.exception('Tickettransfer recipient email could not be sent')


def notify_user_transfer_pending_payment(order, user=None, auth=None, invoices=[]):
    """Notify new owner that they need to pay for the transferred tickets"""
    from pretix.multidomain.urlreverse import eventreverse
    
    with language(order.locale, order.event.settings.region):
        email_template = order.event.settings.get('pretix_ticket_transfer_pending_payment_mailtext', as_type=LazyI18nString)
        if not email_template:
            # Fallback to default message (localized)
            default_text = _('You have received a ticket transfer. Please complete your payment to finalize the transfer.\n\nOrder: {code}\nTotal: {total_with_currency}\n\nPayment link: {url}')
            email_template = LazyI18nString({
                order.locale or 'en': str(default_text)
            })
        email_subject = str(order.event.settings.get('pretix_ticket_transfer_pending_payment_subject', as_type=LazyI18nString) or _('Ticket Transfer - Payment Required')).format(code=order.code)
        email_context = get_email_context(event=order.event, order=order)
        
        # Build payment URL - use order detail page which will show payment options
        payment_url = eventreverse(
            order.event,
            'presale:event.order',
            kwargs={'order': order.code, 'secret': order.secret}
        )
        email_context['payment_url'] = payment_url
        email_context['url'] = payment_url  # Also provide as 'url' for template compatibility
        
        try:
            order.send_mail(
                email_subject, email_template, email_context,
                'pretix.event.order.email.ticket_transfer_pending_payment', user, auth=auth, invoices=invoices)
        except SendMailException:
            logger.exception('Ticket transfer pending payment email could not be sent')


def notify_user_transfer_initiated(order, user=None, auth=None, invoices=[]):
    """Notify old owner that transfer has been initiated"""
    with language(order.locale, order.event.settings.region):
        email_template = order.event.settings.get('pretix_ticket_transfer_initiated_mailtext', as_type=LazyI18nString)
        if not email_template:
            # Fallback to default message (localized)
            default_text = _('Your ticket transfer has been initiated. The new owner will receive an email with payment instructions. You will receive a refund once they complete payment.')
            email_template = LazyI18nString({
                order.locale or 'en': str(default_text)
            })
        email_subject = str(order.event.settings.get('pretix_ticket_transfer_initiated_subject', as_type=LazyI18nString) or _('Ticket Transfer Initiated')).format(code=order.code)
        email_context = get_email_context(event=order.event, order=order)
        try:
            order.send_mail(
                email_subject, email_template, email_context,
                'pretix.event.order.email.ticket_transfer_initiated', user, auth=auth, invoices=invoices)
        except SendMailException:
            logger.exception('Ticket transfer initiated email could not be sent')


def notify_user_transfer_completed_old_owner(order, user=None, auth=None, invoices=[]):
    """Notify old owner that transfer is completed and refund is processed"""
    with language(order.locale, order.event.settings.region):
        email_template = order.event.settings.get('pretix_ticket_transfer_completed_old_owner_mailtext', as_type=LazyI18nString)
        if not email_template:
            # Fallback to default message (localized)
            default_text = _('Your ticket transfer has been completed. The new owner has paid and your refund has been processed.')
            email_template = LazyI18nString({
                order.locale or 'en': str(default_text)
            })
        email_subject = str(order.event.settings.get('pretix_ticket_transfer_completed_old_owner_subject', as_type=LazyI18nString) or _('Ticket Transfer Completed')).format(code=order.code)
        email_context = get_email_context(event=order.event, order=order)
        try:
            order.send_mail(
                email_subject, email_template, email_context,
                'pretix.event.order.email.ticket_transfer_completed_old_owner', user, auth=auth, invoices=invoices)
        except SendMailException:
            logger.exception('Ticket transfer completed (old owner) email could not be sent')


def notify_user_transfer_completed_new_owner(order, user=None, auth=None, invoices=[]):
    """Notify new owner that transfer is completed"""
    with language(order.locale, order.event.settings.region):
        email_template = order.event.settings.get('pretix_ticket_transfer_completed_new_owner_mailtext', as_type=LazyI18nString)
        if not email_template:
            # Fallback to default message (localized)
            default_text = _('Your ticket transfer has been completed. The tickets are now yours.')
            email_template = LazyI18nString({
                order.locale or 'en': str(default_text)
            })
        email_subject = str(order.event.settings.get('pretix_ticket_transfer_completed_new_owner_subject', as_type=LazyI18nString) or _('Ticket Transfer Completed')).format(code=order.code)
        email_context = get_email_context(event=order.event, order=order)
        try:
            order.send_mail(
                email_subject, email_template, email_context,
                'pretix.event.order.email.ticket_transfer_completed_new_owner', user, auth=auth, invoices=invoices, attach_tickets=True)
        except SendMailException:
            logger.exception('Ticket transfer completed (new owner) email could not be sent')

def user_split_positions( order, pids=None ):

  pos = []
  positions = order.positions.select_related('item')
  if pids:
    positions = positions.filter(pk__in=pids)
  for p in positions:
    if not p.item.admission or p.addon_to:
      continue
    if p.all_checkins.exists():
      continue
    if order.event.settings.get( 'pretix_ticket_transfer_items_all' ) == None:
      continue   # default to false
    elif order.event.settings.get( 'pretix_ticket_transfer_items_all' ) == True:
      pos.append( p )
    elif order.event.settings.get( 'pretix_ticket_transfer_items_all' ) == False:
      if p.item.id in json.loads( order.event.settings.get( 'pretix_ticket_transfer_items' )):
        pos.append( p )
  for p in pos:
    p.price_with_addons = p.price
    for addon in p.addons.all():
        if not addon.canceled:
            p.price_with_addons += addon.price
  return pos

def initiate_transfer_with_payment(order, pids, data):
    """
    Initiate a ticket transfer that requires payment from the new owner.
    Creates a new order for the recipient that needs to be paid.
    Stores bank info for refund in original order metadata.
    """
    with transaction.atomic():
        event = order.event
        positions = OrderPosition.objects.filter(pk__in=pids).select_for_update(nowait=True).all()
        ocm = TicketTransferChangeManager(
            order,
            notify=False,
            reissue_invoice=False)

        pos = user_split_positions(order, pids)
        success = 0
        for p in pos:
            p.attendee_name_parts = {}
            ocm.split(p)
            success += 1

            if p.meta_info_data and p.meta_info_data.get('vouchergen_voucher_code'):
                from pretix_vouchergen.utils import cancel_voucher
                cancel_voucher(p.meta_info_data.get('vouchergen_voucher_code'))

                meta = p.meta_info_data
                del meta['vouchergen_voucher_code']
                p.meta_info_data = meta
                p.save()

        if success == len(pos):
            ocm.commit(check_quotas=False)

            split_order = ocm.split_order
            split_order.email_known_to_work = False

            if data.get('email'):
                split_order.email = data.get('email')

            # Set new order to PENDING status - requires payment
            split_order.status = Order.STATUS_PENDING
            split_order.set_expires(now(), list(set(p.subevent_id for p in split_order.positions.all() if p.subevent_id)))
            
            # Store transfer metadata
            meta = split_order.meta_info_data
            meta['doistep'] = {}
            meta['contact_form_data'] = {}
            meta['confirm_messages'] = []
            meta['ticket_transfer'] = TICKET_TRANSFER_PENDING_PAYMENT
            meta['transfer_from_order'] = order.code
            split_order.meta_info = json.dumps(meta)
            split_order.save()

            # Store bank info and transfer info in original order
            meta = order.meta_info_data
            meta['ticket_transfer_pending'] = {
                'to_order': split_order.code,
                'to_email': data.get('email'),
                'bank_info': data.get('bank_info', {}),
                'positions': pids,
                'amount': str(split_order.total)
            }
            order.meta_info = json.dumps(meta)
            order.save()

            # Send email to new owner with payment link
            notify_user_transfer_pending_payment(
                split_order, ocm.user, ocm.auth,
                list(split_order.invoices.all()) if ocm.event.settings.invoice_email_attachment else [])

            # Send confirmation to old owner
            notify_user_transfer_initiated(
                order, ocm.user, ocm.auth,
                ocm._invoices if ocm.event.settings.invoice_email_attachment else [])

            return split_order
    return None


def complete_transfer_after_payment(new_order):
    """
    Complete the transfer when new owner has paid.
    Transfers tickets and refunds old owner.
    """
    with transaction.atomic():
        if not new_order.meta_info_data or new_order.meta_info_data.get('ticket_transfer') != TICKET_TRANSFER_PENDING_PAYMENT:
            return False

        original_order_code = new_order.meta_info_data.get('transfer_from_order')
        if not original_order_code:
            return False

        try:
            original_order = Order.objects.get(code=original_order_code, event=new_order.event)
        except Order.DoesNotExist:
            logger.error(f'Original order {original_order_code} not found for transfer completion')
            return False

        transfer_info = original_order.meta_info_data.get('ticket_transfer_pending', {})
        if not transfer_info:
            return False

        # Mark transfer as completed
        meta = new_order.meta_info_data
        meta['ticket_transfer'] = TICKET_TRANSFER_COMPLETED
        new_order.meta_info = json.dumps(meta)
        new_order.save()

        # Process refund to old owner
        refund_amount = Decimal(transfer_info.get('amount', '0.00'))
        if refund_amount > Decimal('0.00'):
            # Create refund for the original order
            refund = original_order.refunds.create(
                state=OrderRefund.REFUND_STATE_CREATED,
                source=OrderRefund.REFUND_SOURCE_ADMIN,
                amount=refund_amount,
                provider='banktransfer',  # Default to bank transfer, can be configured
                comment=_('Refund for ticket transfer to order {order}').format(order=new_order.code),
                info=json.dumps({
                    'bank_info': transfer_info.get('bank_info', {}),
                    'transfer_to': new_order.code
                })
            )
            original_order.log_action('pretix.event.order.refund.created', {
                'local_id': refund.local_id,
                'provider': refund.provider,
                'reason': 'ticket_transfer'
            })

            # Try to execute refund if provider supports it
            try:
                if refund.payment_provider:
                    refund.payment_provider.execute_refund(refund)
            except Exception as e:
                logger.exception(f'Failed to execute refund for transfer: {e}')
                # Refund is created but may need manual processing

        # Update original order metadata
        meta = original_order.meta_info_data
        meta['ticket_transfer_sent'] = TICKET_TRANSFER_SENT
        meta['ticket_transfer_completed'] = {
            'to_order': new_order.code,
            'completed_at': now().isoformat(),
            'refund_amount': str(refund_amount)
        }
        if 'ticket_transfer_pending' in meta:
            del meta['ticket_transfer_pending']
        original_order.meta_info = json.dumps(meta)
        original_order.save()

        # Send success emails
        notify_user_transfer_completed_old_owner(
            original_order, None, None,
            list(original_order.invoices.all()) if original_order.event.settings.invoice_email_attachment else [])

        notify_user_transfer_completed_new_owner(
            new_order, None, None,
            list(new_order.invoices.all()) if new_order.event.settings.invoice_email_attachment else [])

        return True


def user_split( order, pids, data ):
  with transaction.atomic():
    event = order.event
    positions = OrderPosition.objects.filter(pk__in=pids).select_for_update(nowait=True).all()
    ocm = TicketTransferChangeManager(
        order,
        notify=False,
        reissue_invoice=False )

    pos = user_split_positions( order, pids )
    success = 0
    for p in pos:
      p.attendee_name_parts = {}
      ocm.split(p)
      success+= 1

      if p.meta_info_data and p.meta_info_data.get('vouchergen_voucher_code'):
        from pretix_vouchergen.utils import cancel_voucher
        cancel_voucher( p.meta_info_data.get('vouchergen_voucher_code'))

        meta = p.meta_info_data
        del meta['vouchergen_voucher_code']
        p.meta_info_data = meta
        p.save()

    if success == len(pos):

      ocm.commit(check_quotas=False)

      split_order = ocm.split_order
      split_order.email_known_to_work = False

      if data.get('email'):
        split_order.email = data.get('email')

      meta = split_order.meta_info_data
      meta['doistep'] = {}
      meta['contact_form_data'] = {}
      meta['confirm_messages'] = []
      meta['ticket_transfer'] = TICKET_TRANSFER_START if transfer_needs_accept(event) else TICKET_TRANSFER_DONE
      split_order.meta_info = json.dumps(meta)
      split_order.save()

      meta = order.meta_info_data
      meta['ticket_transfer_sent'] = TICKET_TRANSFER_SENT
      order.meta_info = json.dumps(meta)
      order.save()

      notify_user_split_order_source(
          order, ocm.user, ocm.auth,
          ocm._invoices if ocm.event.settings.invoice_email_attachment else [] )
      notify_user_split_order_target(
          split_order, ocm.user, ocm.auth,
          list(split_order.invoices.all()) if ocm.event.settings.invoice_email_attachment else [] )

      return True
    return False


