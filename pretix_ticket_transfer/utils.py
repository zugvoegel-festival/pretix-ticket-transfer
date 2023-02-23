from pretix.base.templatetags.rich_text import rich_text
from pretix.presale.signals import checkout_confirm_messages


def get_confirm_messages(event):
    msgs = {}
    if event.settings.pretix_ticket_transfer_global_confirm_texts:
        responses = checkout_confirm_messages.send(event)
        for receiver, response in responses:
            msgs.update(response)
    for index, text in enumerate(event.settings.pretix_ticket_transfer_confirm_texts):
        msgs['ticket_transfer_confirm_text_%i' % index] = rich_text(str(text))
    return msgs


def transfer_needs_accept(event):
    return bool(get_confirm_messages(event))
