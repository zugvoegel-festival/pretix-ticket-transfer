import json
from django.http import Http404
from django import forms
from django.contrib import messages
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect
from django_scopes import scope

from django.utils.translation import gettext_lazy as _
from django.utils.http import urlencode

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

from .user_split import user_split


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
        ctx['orderpositions'] = self.order.positions.select_related('item')
        return ctx

    def post(self, request, *args, **kwargs):
        order = self.order

        if order.status != Order.STATUS_PAID:
          raise Http404()

        error = False

        pids = request.POST.getlist('pos[]')
        email = request.POST.get('email')
        email_repeat = request.POST.get('email_repeat')

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
            user_split(order,pids,data={'email': email})

            messages.success( self.request, _('Ticket(s) transferiert') ),
            return redirect(
                eventreverse(
                    self.request.event,
                    "presale:event.order",
                    kwargs={"order": self.order.code, "secret": self.order.secret} ))

        pos = []
        totalprice = 0
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
        return self.render_to_response(ctx)
