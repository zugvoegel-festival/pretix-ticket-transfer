from pretix.presale.signals import checkout_confirm_messages

def get_confirm_messages(event):
    msgs = {}
    responses = checkout_confirm_messages.send(event)
    for receiver, response in responses:
        msgs.update(response)
    return msgs

def transfer_needs_accept(event):
    return bool(get_confirm_messages(event))
