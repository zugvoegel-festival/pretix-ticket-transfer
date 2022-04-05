from django.conf.urls import url, include, re_path
from pretix.multidomain import event_url

from .views import (
    TicketTransferSettingsView,
    TicketTransfer
)

event_patterns = [
    event_url(
        r"^order/(?P<order>[^/]+)/(?P<secret>[A-Za-z0-9]+)/ticket_transfer$",
        TicketTransfer.as_view(),
        name="generate",
    ),
]

urlpatterns = [
    url(r'^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/ticket_transfer/settings$',
        TicketTransferSettingsView.as_view(), name='settings'),
]
