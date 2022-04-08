import json
from django.dispatch import receiver
from django.urls import resolve, reverse
from django.utils.translation import gettext_lazy as _
from django.utils.http import urlencode
from django.template.loader import get_template
from django.middleware import csrf
from pretix.base.templatetags.rich_text import rich_text
from i18nfield.strings import LazyI18nString

from pretix.control.signals import nav_event_settings
from pretix.presale.signals import order_info_top, order_info, sass_postamble
from pretix.base.models import Order
from django.shortcuts import redirect
from django.urls import reverse
from pretix.multidomain.urlreverse import eventreverse

from pretix.presale.signals import checkout_confirm_messages
from pretix.base.signals import logentry_display

from django.contrib.staticfiles import finders


from .user_split import TICKET_TRANSFER_START, TICKET_TRANSFER_DONE

@receiver(sass_postamble, dispatch_uid="ticket_transfer_sass_postamble")
def r_sass_postamble(sender, filename, **kwargs):
    out = []
    if filename == "main.scss":
        with open(finders.find('pretix_ticket_transfer/scss/theme.scss'), 'r') as fp:
            out.append(fp.read())
    return "\n".join(out)

@receiver(signal=logentry_display, dispatch_uid="ticket_transfer_logentry_display")
def pretixcontrol_logentry_display(sender, logentry, **kwargs):
    event_type = logentry.action_type
    plains = {
      'pretix.event.order.email.ticket_transfer_recipient': _('Tickettransfer recipient email was sent'),
      'pretix.event.order.email.ticket_transfer_sender': _('Tickettransfer sender email was sent')
    }
    if event_type in plains:
      return plains[event_type]


@receiver(order_info_top, dispatch_uid="ticket_transfer_order_info_target")
def orderinfo_target(sender, order, request, **kwargs):
  positions = order.positions.select_related('item')
  confirm = False
  for p in positions:
    if p.meta_info_data and p.meta_info_data.get('ticket_transfer'):

      ctx = {
        'order': order,
        'event': sender,
        'title': str( sender.settings.get('pretix_ticket_transfer_title', as_type=LazyI18nString )),
        'csrf_token': csrf.get_token(request),
      }
      if p.meta_info_data.get('ticket_transfer') == TICKET_TRANSFER_START:

        ctx['message'] = str(rich_text( sender.settings.get('pretix_ticket_transfer_recipient_message', as_type=LazyI18nString )))
        for receiver, response in checkout_confirm_messages.send(request.event):
          if 'pages' in response:
            ctx['confirm'] = response['pages']
        template = get_template( 'pretix_ticket_transfer/order_info_accept.html' )
        return template.render( ctx )

      elif p.meta_info_data.get('ticket_transfer') == TICKET_TRANSFER_DONE:
        ctx['message'] = str(rich_text( sender.settings.get('pretix_ticket_transfer_recipient_done_message', as_type=LazyI18nString )))
        template = get_template( 'pretix_ticket_transfer/order_info_done.html' )
        if ctx['message']:
          return template.render( ctx )

      return False



@receiver(order_info, dispatch_uid="ticket_transfer_order_info_source")
def orderinfo_source(sender, order, request, **kwargs):
    if order.status != Order.STATUS_PAID:
        return False

    event = order.event
    pos = []

    positions = order.positions.select_related('item')
    for p in positions:
      if p.meta_info_data and p.meta_info_data.get('ticket_transfer') == TICKET_TRANSFER_START:
        return False

      if not p.item.admission:
        continue
      if event.settings.get( 'pretix_ticket_transfer_items_all' ) == None:
        continue   # default to false
      elif event.settings.get( 'pretix_ticket_transfer_items_all' ) == True:
        pos.append( p )
      elif event.settings.get( 'pretix_ticket_transfer_items_all' ) == False:
        if p.item.id in json.loads( event.settings.get( 'pretix_ticket_transfer_items' )):
          pos.append( p )

    if not len( pos ):
      return

    ctx = {
        'csrf_token': csrf.get_token(request),
        'order': order,
        'pos': pos,
        'event': sender,
        'title': str( sender.settings.get('pretix_ticket_transfer_title', as_type=LazyI18nString )),
        'message': str(rich_text( sender.settings.get('pretix_ticket_transfer_message', as_type=LazyI18nString ))),
        'url': False }

    template = get_template( 'pretix_ticket_transfer/order_info.html' )
    return template.render( ctx )

@receiver(nav_event_settings, dispatch_uid='ticket_transfer_nav_settings')
def navbar_settings(sender, request, **kwargs):
    url = resolve(request.path_info)
    return [{
        'label': _('Ticket-Transfer'),
        'url': reverse('plugins:pretix_ticket_transfer:settings', kwargs={
            'event': request.event.slug,
            'organizer': request.organizer.slug,
        }),
        'active': url.namespace == 'plugins:pretix_ticket_transfer' and url.url_name == 'settings',
    }]

