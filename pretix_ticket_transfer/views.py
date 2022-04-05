import json
from django.http import Http404
from django import forms
from django.contrib import messages
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect
from django_scopes import scope

from django.utils.translation import gettext_lazy as _
from django.utils.http import urlencode
from django.db import transaction

from django.views.generic import TemplateView
from pretix.base.models import Event, Order, Item, OrderPosition

from pretix.base.forms import SettingsForm
from i18nfield.forms import (
    I18nFormField, I18nTextarea,
)
from pretix.control.views.event import (
    EventSettingsFormView, EventSettingsViewMixin,
)

from pretix.presale.views import EventViewMixin
from pretix.presale.views.order import OrderDetailMixin
from pretix.multidomain.urlreverse import eventreverse

from django.middleware import csrf
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from pretix.base.services.orders import OrderChangeManager



from pprint import pprint

class TicketTransferChangeManager(OrderChangeManager):
    def commit(self, check_quotas=True):
        if self._committed:
            # an order change can only be committed once
            raise OrderError(error_messages['internal'])
        self._committed = True

        if not self._operations:
            # Do nothing
            return

        # finally, incorporate difference in payment fees
        self._payment_fee_diff()

        with transaction.atomic():
            with self.order.event.lock():
                if self.order.status in (Order.STATUS_PENDING, Order.STATUS_PAID):
                    if check_quotas:
                        self._check_quotas()
                    self._check_seats()
                self._check_complete_cancel()
                self._check_and_lock_memberships()
                try:
                    self._perform_operations()
                except TaxRule.SaleNotAllowed:
                    raise OrderError(self.error_messages['tax_rule_country_blocked'])
            self._recalculate_total_and_payment_fee()
            self._check_paid_price_change()
            self._check_paid_to_free()
            if self.order.status in (Order.STATUS_PENDING, Order.STATUS_PAID):
                self._reissue_invoice()
            self._clear_tickets_cache()
            self.order.touch()
            self.order.create_transactions()
            if self.split_order:
                self.split_order.create_transactions()

        if self.notify:
            notify_user_changed_order(
                self.order, self.user, self.auth,
                self._invoices if self.event.settings.invoice_email_attachment else []
            )
            if self.split_order:
                notify_user_changed_order(
                    self.split_order, self.user, self.auth,
                    list(self.split_order.invoices.all()) if self.event.settings.invoice_email_attachment else []
                )

        order_changed.send(self.order.event, order=self.order)




class TicketTransferSettingsForm(SettingsForm):
    pretix_ticket_transfer_title = I18nFormField(
        label=_("Orderinfo title"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Orderinfo title"),
        widget_kwargs={'attrs': { 'rows': '1' }} )
    pretix_ticket_transfer_message = I18nFormField(
        label=_("Orderinfo message"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Orderinfo message"),
        widget_kwargs={'attrs': { 'rows': '8' }} )
    def __init__(self, *args, **kwargs):
       event = self.event = kwargs.pop('event')
       super().__init__(*args, **kwargs)

       self.fields['pretix_ticket_transfer_items_all'] = forms.BooleanField(
           label=_("All products (including newly created ones)"),
           required=False )

       if self.initial.get( 'pretix_ticket_transfer_items_all') == None:
         self.initial['pretix_ticket_transfer_items_all'] = True

       if self.initial.get( 'pretix_ticket_transfer_items') != None:
         self.initial['pretix_ticket_transfer_items'] = json.loads( self.initial['pretix_ticket_transfer_items'] )

       self.fields['pretix_ticket_transfer_items'] = forms.ModelMultipleChoiceField(
         widget=forms.CheckboxSelectMultiple(
             attrs={
               'data-inverse-dependency': '<[name$=pretix_ticket_transfer_items_all]',
               'class': 'scrolling-multiple-choice' }),
         label=_('Items'),
         required=False,
         queryset=event.items.all()
       )

    def clean(self):
        d = super().clean()
        if d['pretix_ticket_transfer_items_all']:
          d['pretix_ticket_transfer_items'] = None
        else:
          d['pretix_ticket_transfer_items'] = json.dumps([ i.id for i in d['pretix_ticket_transfer_items'] ])
        return d

class TicketTransferSettingsView(EventSettingsViewMixin, EventSettingsFormView):
    model = Event
    permission = 'can_change_settings'
    form_class = TicketTransferSettingsForm
    template_name = 'pretix_ticket_transfer/settings.html'

    def get_success_url(self, **kwargs):
        return reverse('plugins:pretix_ticket_transfer:settings', kwargs={
            'organizer': self.request.event.organizer.slug,
            'event': self.request.event.slug,
        })

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['event'] = self.request.event
        return kwargs


class TicketTransfer(EventViewMixin, OrderDetailMixin, TemplateView):
    template_name = "pretix_ticket_transfer/transfer.html"

    def get_context_data(self, *args, **kwargs):
        ctx = super().get_context_data(*args, **kwargs)
        ctx['order'] = self.order
        return ctx

    def post(self, request, *args, **kwargs):
        order = self.order
        event = order.event

        if order.status != Order.STATUS_PAID:
          raise Http404()

        error = False
        pos = []
        totalprice = 0

        pids = request.POST.getlist('pos[]')
        email = request.POST.get('email')
        email_repeat = request.POST.get('email_repeat')

        #confirm = request.POST.getlist('confirm')

        if email:
          try:
            validate_email(email)
          except ValidationError:
            error = _("Please enter a valid email")

          if email != email_repeat:
            error = _("The emails do not match")

          if error:
            messages.warning( self.request, error),

          else:

            with transaction.atomic():

              print( 'start' )
              positions = OrderPosition.objects.filter(pk__in=pids).select_for_update(nowait=True).all()
              ocm = TicketTransferChangeManager(
                  order,
                  #user='ticket-transfer',
                  notify=False,
                  reissue_invoice=False
              )

              for p in positions:
                if not p.item.admission:
                  continue
                if event.settings.get( 'pretix_ticket_transfer_items_all' ) == None:
                  continue   # default to false
                elif event.settings.get( 'pretix_ticket_transfer_items_all' ) == False:
                  if p.item.id not in json.loads( event.settings.get( 'pretix_ticket_transfer_items' )):
                    continue

                p.attendee_name_parts = ''
                ocm.split(p)

                pos.append( p )
                totalprice+= p.price

              pprint({'pos':pos})

              if len(pos) < 1:
                raise Http404()


              ocm.commit(check_quotas=False)
              #with ocm.order.event.lock():
              #  #ocm._check_complete_cancel()
              #  ocm._check_and_lock_memberships()
              #  try:
              #      ocm._perform_operations()
              #  except TaxRule.SaleNotAllowed:
              #      raise OrderError(ocm.error_messages['tax_rule_country_blocked'])

              #ocm._recalculate_total_and_payment_fee()
              #ocm._check_paid_price_change()
              #ocm._check_paid_to_free()
              #if ocm.order.status in (Order.STATUS_PENDING, Order.STATUS_PAID):
              #    ocm._reissue_invoice()
              #ocm._clear_tickets_cache()
              #ocm.order.touch()
              #ocm.order.create_transactions()
              #if ocm.split_order:
              #    ocm.split_order.create_transactions()

              #ocm.split_order.email = email
              #ocm.split_order.save()

              pprint({ 'ocm-split_order': ocm.split_order })


            messages.success( self.request, 'send' ),
            ctx = {
              'order': order,
              'pos': pos,
              'totalprice': totalprice,
              'email': email,
              'success': True
            }
            return self.render_to_response(ctx)

        pos = []
        for id in request.POST.getlist('pos[]'):
          position = OrderPosition.objects.get(pk=id)
          pos.append( position )
          totalprice+= position.price

        if len(pos) < 1:
          raise Http404()


        ctx = {
          'csrf_token': csrf.get_token(request),
          'order': order,
          'pos': pos,
          'totalprice': totalprice,
          'email': email
        }
        #return self.get(request, *args, **kwargs)
        return self.render_to_response(ctx)


            #meta = position.meta_info_data
            #position.meta_info_data = meta
            #position.save()
            #messages.success(request, _("Voucher already created."))

            #url = reverse(
            #  'presale:event.redeem',
            #  kwargs={
            #    'organizer': event.organizer.slug,
            #    'event': event.slug })
            ##url+= '?' + urlencode({ 'voucher': voucher.code })
            #return redirect( url )

        #return redirect(
        #    eventreverse(
        #        self.request.event,
        #        "presale:event.order",
        #        kwargs={"order": self.order.code, "secret": self.order.secret} ))

