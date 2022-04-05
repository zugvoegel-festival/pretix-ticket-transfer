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

from pretix.presale.signals import order_info_top, order_info

from pretix.base.models import Order

@receiver(order_info, dispatch_uid="ticket_transfer_order_info")
def orderinfo(sender, order, request, **kwargs):
    if order.status != Order.STATUS_PAID:
        return False

    event = order.event
    pos = []

    positions = order.positions.select_related('item')
    for p in positions:
      #from pprint import pprint
      #pprint( vars( p ))
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
        'label': _('ticket_transfer settings'),
        'url': reverse('plugins:pretix_ticket_transfer:settings', kwargs={
            'event': request.event.slug,
            'organizer': request.organizer.slug,
        }),
        'active': url.namespace == 'plugins:pretix_ticket_transfer' and url.url_name == 'settings',
    }]

