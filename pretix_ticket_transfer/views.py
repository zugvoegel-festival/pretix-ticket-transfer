import json
import operator
from django import forms
from django.http import Http404
from django.utils.functional import cached_property
from django.views.generic import TemplateView
from django.urls import reverse
from django.shortcuts import redirect
from django.middleware import csrf
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from i18nfield.strings import LazyI18nString
from pretix.base.models import Event, Order, OrderPosition
from pretix.base.forms import SettingsForm
from pretix.base.settings import LazyI18nStringList
from pretix.control.permissions import EventPermissionRequiredMixin
from pretix.control.views.event import EventSettingsFormView, EventSettingsViewMixin
from pretix.control.forms.event import ConfirmTextFormset
from pretix.presale.views import EventViewMixin
from pretix.presale.views.order import OrderDetailMixin
from pretix.multidomain.urlreverse import eventreverse
from pretix.base.templatetags.rich_text import rich_text
from i18nfield.forms import I18nFormField, I18nTextarea

from .user_split import (
    user_split_positions, initiate_transfer_with_payment,
    TICKET_TRANSFER_START, TICKET_TRANSFER_DONE, TICKET_TRANSFER_SENT
)
from .utils import get_confirm_messages

class TicketTransferSettingsForm(SettingsForm):
    pretix_ticket_transfer_title = I18nFormField(
        label=_("Order info title"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Panel title"),
        widget_kwargs={'attrs': { 'rows': '1' }} )

    pretix_ticket_transfer_message = I18nFormField(
        label=_("Sender - orderinfo message"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Order info for sender"),
        widget_kwargs={'attrs': { 'rows': '8' }} )
    pretix_ticket_transfer_step2_message = I18nFormField(
        label=_("Sender - step2 message"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Order info for sender select email step"),
        widget_kwargs={'attrs': { 'rows': '8' }} )
    pretix_ticket_transfer_step3_message = I18nFormField(
        label=_("Sender - step3 message"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Order info for sender confirm step"),
        widget_kwargs={'attrs': { 'rows': '8' }} )
    pretix_ticket_transfer_done_message = I18nFormField(
        label=_("Sender - done message"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Order info for sender after all tickets are sold"),
        widget_kwargs={'attrs': { 'rows': '8' }} )

    pretix_ticket_transfer_recipient_message = I18nFormField(
        label=_("Recipient - orderinfo message"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Order info for recipient"),
        widget_kwargs={'attrs': { 'rows': '8' }} )
    pretix_ticket_transfer_recipient_done_message = I18nFormField(
        label=_("Recipient - done message"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Order info for recipient after accepting"),
        widget_kwargs={'attrs': { 'rows': '8' }} )


    pretix_ticket_transfer_sender_subject = I18nFormField(
        label=_("Sender - email subject"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Subject for sender email"),
        widget_kwargs={'attrs': { 'rows': '1' }} )
    pretix_ticket_transfer_sender_mailtext = I18nFormField(
        label=_("Sender - email text"),
        required=False,
        widget=I18nTextarea,
        help_text=_('placeholders: {list}'.format(list = ', '.join(['{code}', '{event}', '{event_slug}', '{name}', '{total}', '{total_with_currency}', '{url}']))),
        widget_kwargs={'attrs': { 'rows': '8' }} )

    pretix_ticket_transfer_recipient_subject = I18nFormField(
        label=_("Recipient - email subject"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Subject for recipient email"),
        widget_kwargs={'attrs': { 'rows': '1' }} )
    pretix_ticket_transfer_recipient_mailtext = I18nFormField(
        label=_("Recipient - email text"),
        required=False,
        widget=I18nTextarea,
        help_text=_('placeholders: {list}'.format(list = ', '.join(['{code}', '{event}', '{event_slug}', '{name}', '{total}', '{total_with_currency}', '{url}']))),
        widget_kwargs={'attrs': { 'rows': '8' }} )

    # New flow: Pending payment email (to new owner)
    pretix_ticket_transfer_pending_payment_subject = I18nFormField(
        label=_("New owner - pending payment email subject"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Subject for email to new owner (payment required)"),
        widget_kwargs={'attrs': { 'rows': '1' }} )
    pretix_ticket_transfer_pending_payment_mailtext = I18nFormField(
        label=_("New owner - pending payment email text"),
        required=False,
        widget=I18nTextarea,
        help_text=_('placeholders: {list}'.format(list = ', '.join(['{code}', '{event}', '{event_slug}', '{name}', '{total}', '{total_with_currency}', '{url}', '{payment_url}']))),
        widget_kwargs={'attrs': { 'rows': '8' }} )

    # New flow: Transfer initiated email (to old owner)
    pretix_ticket_transfer_initiated_subject = I18nFormField(
        label=_("Old owner - transfer initiated email subject"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Subject for email to old owner (transfer initiated)"),
        widget_kwargs={'attrs': { 'rows': '1' }} )
    pretix_ticket_transfer_initiated_mailtext = I18nFormField(
        label=_("Old owner - transfer initiated email text"),
        required=False,
        widget=I18nTextarea,
        help_text=_('placeholders: {list}'.format(list = ', '.join(['{code}', '{event}', '{event_slug}', '{name}', '{total}', '{total_with_currency}', '{url}']))),
        widget_kwargs={'attrs': { 'rows': '8' }} )

    # New flow: Transfer completed email (to old owner)
    pretix_ticket_transfer_completed_old_owner_subject = I18nFormField(
        label=_("Old owner - transfer completed email subject"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Subject for email to old owner (transfer completed, refund processed)"),
        widget_kwargs={'attrs': { 'rows': '1' }} )
    pretix_ticket_transfer_completed_old_owner_mailtext = I18nFormField(
        label=_("Old owner - transfer completed email text"),
        required=False,
        widget=I18nTextarea,
        help_text=_('placeholders: {list}'.format(list = ', '.join(['{code}', '{event}', '{event_slug}', '{name}', '{total}', '{total_with_currency}', '{url}']))),
        widget_kwargs={'attrs': { 'rows': '8' }} )

    # New flow: Transfer completed email (to new owner)
    pretix_ticket_transfer_completed_new_owner_subject = I18nFormField(
        label=_("New owner - transfer completed email subject"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Subject for email to new owner (transfer completed)"),
        widget_kwargs={'attrs': { 'rows': '1' }} )
    pretix_ticket_transfer_completed_new_owner_mailtext = I18nFormField(
        label=_("New owner - transfer completed email text"),
        required=False,
        widget=I18nTextarea,
        help_text=_('placeholders: {list}'.format(list = ', '.join(['{code}', '{event}', '{event_slug}', '{name}', '{total}', '{total_with_currency}', '{url}']))),
        widget_kwargs={'attrs': { 'rows': '8' }} )

    # Optional: Formular-Texte
    pretix_ticket_transfer_bank_details_intro = I18nFormField(
        label=_("Bank details form - introduction text"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Introduction text shown above the bank details form"),
        widget_kwargs={'attrs': { 'rows': '3' }} )
    pretix_ticket_transfer_step2_title = I18nFormField(
        label=_("Step 2 - title"),
        required=False,
        widget=I18nTextarea,
        help_text=_("Title for the bank details step"),
        widget_kwargs={'attrs': { 'rows': '1' }} )

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
         queryset=event.items.all() )

       self.fields['pretix_ticket_transfer_global_confirm_texts'] = forms.BooleanField(label=_("Show general confirmation texts"), required=False)

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

    def get_context_data(self, *args, **kwargs):
        ctx = super().get_context_data(*args, **kwargs)
        ctx['confirm_texts_formset'] = self.confirm_texts_formset
        return ctx

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        if not self.confirm_texts_formset.is_valid():
            messages.error(self.request, _('We could not save your changes. See below for details.'))
            return self.render_to_response(self.get_context_data(form=self.get_form()))
        self.save_confirm_texts_formset()
        return super().post(request, *args, **kwargs)

    @cached_property
    def confirm_texts_formset(self):
        initial = [
            {"text": text, "ORDER": order}
            for order, text in enumerate(self.request.event.settings.pretix_ticket_transfer_confirm_texts)
        ]
        return ConfirmTextFormset(
            self.request.POST if self.request.method == "POST" else None,
            event=self.request.event,
            prefix="confirm-texts",
            initial=initial
        )

    def save_confirm_texts_formset(self):
        self.request.event.settings.pretix_ticket_transfer_confirm_texts = LazyI18nStringList(
            form_data['text'].data
            for form_data in sorted((d for d in self.confirm_texts_formset.cleaned_data if d), key=operator.itemgetter("ORDER"))
            if form_data and not form_data.get("DELETE", False)
        )

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
        ctx['orderpositions'] = user_split_positions( self.order )

        ctx['title'] = self.order.event.settings.get('pretix_ticket_transfer_title', as_type=LazyI18nString )
        ctx['message'] = str(rich_text( self.order.event.settings.get('pretix_ticket_transfer_step2_message', as_type=LazyI18nString )))
        return ctx

    def post(self, request, *args, **kwargs):
        if self.order.status != Order.STATUS_PAID:
          raise Http404()

        error = False
        pos = []
        pids = request.POST.getlist('pos[]')
        email = request.POST.get('email')
        email_repeat = request.POST.get('email_repeat')
        confirm = request.POST.get('confirm')
        step2 = request.POST.get('step2')
        step3 = request.POST.get('step3')  # Bank info step

        ctx = self.get_context_data(*args, **kwargs)
        ctx['csrf_token'] = csrf.get_token(request)

        # Get selected positions (skip validation if we're confirming transfer)
        if pids and not (step3 and confirm):
          pos = user_split_positions( self.order, pids )
          if not len( pids ) == len( pos ):
            error = _("Invalid ticket selection")
        else:
          # If no pids in POST, try to get from previous step
          pos = []
          # For confirmation step, get pids from POST directly
          if step3 and confirm and not pids:
            pids = request.POST.getlist('pos[]')
            if pids:
              pos = user_split_positions( self.order, pids )

        # Step 3: Process transfer confirmation (highest priority - check first)
        if step3 and confirm:
          # Get pids from POST if not already set
          if not pids:
            pids = request.POST.getlist('pos[]')
          
          bank_info = {
            'account_holder': request.POST.get('bank_account_holder', ''),
            'iban': request.POST.get('bank_iban', ''),
            'bic': request.POST.get('bank_bic', ''),
            'bank_name': request.POST.get('bank_name', ''),
          }
          email = request.POST.get('email', '')
          
          if not pids:
            messages.error( self.request, _('No tickets selected. Please start over.') )
            return redirect(
                eventreverse(
                    self.request.event,
                    "presale:event.order",
                    kwargs={"order": self.order.code, "secret": self.order.secret} ))
          
          data = {
            'email': email,
            'bank_info': bank_info
          }
          
          new_order = initiate_transfer_with_payment(self.order, pids, data)
          if new_order:
            messages.success( self.request, _('Ticket transfer initiated. The new owner will receive payment instructions.') )
            return redirect(
                eventreverse(
                    self.request.event,
                    "presale:event.order",
                    kwargs={"order": self.order.code, "secret": self.order.secret} ))
          else:
            messages.error( self.request, _('Failed to initiate transfer. Please try again.') )

        # Step 2: Handle bank info form submission (Continue button clicked from step 2)
        elif step2 and step3 and not confirm:
          bank_info = {
            'account_holder': request.POST.get('bank_account_holder', ''),
            'iban': request.POST.get('bank_iban', ''),
            'bic': request.POST.get('bank_bic', ''),
            'bank_name': request.POST.get('bank_name', ''),
          }
          
          # Validate bank info
          if not bank_info.get('account_holder'):
            error = _("Please enter account holder name")
          if not bank_info.get('iban'):
            error = _("Please enter IBAN")
          
          if error:
            messages.warning( self.request, error)
            ctx['step2'] = True
            ctx['email'] = request.POST.get('email', '')
            ctx['email_repeat'] = request.POST.get('email', '')
            ctx['bank_info'] = bank_info
            if pids:
              ctx['pids'] = pids
          else:
            # Move to step 3: Confirm
            ctx['step3'] = True
            ctx['email'] = request.POST.get('email', '')
            ctx['bank_info'] = bank_info
            ctx['message'] = str(rich_text( self.order.event.settings.get('pretix_ticket_transfer_step3_message', as_type=LazyI18nString )))
            # Preserve positions
            if pids:
              ctx['pids'] = pids

        # Step 1: Select tickets and enter email
        elif not step2 and not step3 and email:
          try:
            validate_email(email)
          except ValidationError:
            error = _("Please enter a valid email address")
          if email != email_repeat:
            error = _("The email addresses do not match")
          if not len( pids ):
            error = _("Please select ticket(s) for transfer")

          if error:
            messages.warning( self.request, error)
          else:
            # Move to step 2: Bank info
            ctx['step2'] = True
            ctx['email'] = email
            ctx['email_repeat'] = email_repeat
            if pids:
              ctx['pids'] = pids

        # Step 2: Show bank info form (coming from step 1, or Go back from step 3)
        elif step2 and not step3:
          # Check if this is "Go back" from step 3 (step2 button with empty value)
          if step2 == '':
            # Go back to step 1
            ctx['email'] = request.POST.get('email', '')
            ctx['email_repeat'] = request.POST.get('email', '')
            if pids:
              ctx['pids'] = pids
          else:
            # Show step 2 (bank info form) - coming from step 1
            ctx['step2'] = True
            ctx['email'] = request.POST.get('email', '')
            ctx['email_repeat'] = request.POST.get('email', '')
            ctx['bank_info'] = ctx.get('bank_info', {})
            # Preserve positions
            if pids:
              ctx['pids'] = pids
            elif not pos:
              # If no positions yet, get from previous context
              pos = user_split_positions(self.order)
              ctx['pids'] = [p.id for p in pos]

        # Step 3: Display confirmation (not submitting yet)
        elif step3 and not confirm:
            # Just displaying step 3 confirmation (not submitting yet)
            # Get bank info and positions from POST or context
            if not ctx.get('bank_info'):
              ctx['bank_info'] = {
                'account_holder': request.POST.get('bank_account_holder', ''),
                'iban': request.POST.get('bank_iban', ''),
                'bic': request.POST.get('bank_bic', ''),
                'bank_name': request.POST.get('bank_name', ''),
              }
            ctx['step3'] = True
            ctx['email'] = ctx.get('email', request.POST.get('email', ''))
            if pids:
              ctx['pids'] = pids
            ctx['message'] = str(rich_text( self.order.event.settings.get('pretix_ticket_transfer_step3_message', as_type=LazyI18nString )))

        # Preserve positions and calculate total
        if not pos and pids:
          pos = user_split_positions(self.order, pids)
        
        totalprice = 0
        for position in pos:
          totalprice+= position.price_with_addons

        ctx['pos'] = pos
        ctx['totalprice'] = totalprice
        ctx['email'] = ctx.get('email', email or "")
        ctx['email_repeat'] = ctx.get('email_repeat', email_repeat or ctx.get('email', ''))
        ctx['bank_info'] = ctx.get('bank_info', {})
        
        # Preserve pids in hidden fields for all steps
        ctx['pids'] = pids

        return self.render_to_response(ctx)

class TicketTransferAccept(EventViewMixin, OrderDetailMixin, TemplateView):
    def post(self, request, *args, **kwargs):
        positions = self.order.positions.select_related('item')

        msgs = get_confirm_messages(self.request.event)
        for key, msg in msgs.items():
            if request.POST.get('confirm_{}'.format(key)) != 'yes':
                msg = str(_('You need to check all checkboxes to confirm the ticket transfer.'))
                messages.error(self.request, msg)
                return redirect(eventreverse(
                    self.request.event,
                    "presale:event.order",
                    kwargs={"order": self.order.code, "secret": self.order.secret}
                ))

        meta = self.order.meta_info_data
        meta['ticket_transfer'] = TICKET_TRANSFER_DONE
        meta.setdefault('confirm_messages', [])
        meta['confirm_messages'] += [str(msg) for msg in msgs.values()]
        self.order.meta_info = json.dumps(meta)
        self.order.save()

        for msg in msgs.values():
            self.order.log_action('pretix.event.order.consent', data={'msg': msg})
        messages.success(self.request, _('Ticket transfer completed'))

        return redirect(
            eventreverse(
                self.request.event,
                "presale:event.order",
                kwargs={"order": self.order.code, "secret": self.order.secret} ))

class TicketTransferStats(EventPermissionRequiredMixin, TemplateView):
    permission = "can_change_event_settings"
    template_name = "pretix_ticket_transfer/control/stats.html"

    def get_context_data(self, *args, **kwargs):
        ctx = super().get_context_data(*args, **kwargs)
        ctx['rows'] = []

        map = {
            TICKET_TRANSFER_START: 'start',
            TICKET_TRANSFER_DONE: 'done',
            TICKET_TRANSFER_SENT: 'sent',
        } 
        counter = {'all':0, 'start':0, 'done':0, 'sent':0}
        def count(*i):
          counter['all']+= 1
          for k in i:
            k = map.get(k,k)
            print(f'k {k}')
            counter[k] = counter.get(k,0) + 1


        orders = Order.objects.filter(
                event=self.request.event,
                meta_info__contains='"ticket_transfer":')
        for o in orders:
          count(o.meta_info_data.get('ticket_transfer'), f'{o.status}')
          #count(o.meta_info_data.get('ticket_transfer'))


        sent = Order.objects.filter(
                event=self.request.event,
                meta_info__contains='"ticket_transfer_sent": 23')
        for o in sent:
          count(o.meta_info_data.get('ticket_transfer_sent'), f'sent_{o.status}')

        print(counter)
        ctx['counter'] = counter

        return ctx


        #orders = Order.objects.raw("select id,code,meta_info from pretixbase_order where meta_info like '%%ticket_transfer%%'")

        #from django.db import connection
        #with connection.cursor() as cursor:
        #  cursor.execute(
        #      """
        #        select meta_info::json->'ticket_transfer' #>> '{}' as ticket_transfer from pretixbase_order where meta_info like '%ticket_transfer%'
        #      """)

        #orders = Order.objects.filter(order__meta_info__ticket_transfer)
        #orders = Order.objects.raw("select id,code,meta_info from pretixbase_order where meta_info like '%%ticket_transfer%%'")
