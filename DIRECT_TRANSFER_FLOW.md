# Direct Transfer Flow

**Direct transfer** between owners without the ticket shop acting as an intermediary for payment/refund.

## Sequence Diagram (simplified)

```mermaid
sequenceDiagram
    participant TO as Old Owner
    participant TS as Ticket Shop
    participant NO as New Owner

    Note over TO,NO: Phase 1: Transfer Initiation
    
    TO->>TS: 1. Selects tickets
    TO->>TS: 2. Enters new owner's email
    TO->>TS: 3. Confirms transfer
    
    TS->>TS: Creates new order for new owner
    TS->>TS: Moves tickets to new order
    
    alt Transfer needs acceptance
        TS->>TS: Set status: TICKET_TRANSFER_START
        TS->>NO: Email: Transfer received<br/>(needs acceptance)
        TS->>TO: Email: Transfer sent
    else Transfer does not need acceptance
        TS->>TS: Set status: TICKET_TRANSFER_DONE
        TS->>NO: Email: Transfer completed<br/>(tickets attached)
        TS->>TO: Email: Transfer sent
    end
    
    Note over TO,NO: Phase 2: Acceptance (if needed)
    
    alt Transfer needs acceptance
        NO->>TS: Opens order page
        TS->>NO: Shows confirmation texts
        NO->>TS: Accepts confirmation texts
        TS->>TS: Set status: TICKET_TRANSFER_DONE
        TS->>NO: Transfer completed
    end
    
    Note over TO,NO: Transfer successfully completed
```

## Status Values

- `TICKET_TRANSFER_START (1)`: Transfer initiated, new owner needs to accept
- `TICKET_TRANSFER_DONE (2)`: Transfer completed
- `TICKET_TRANSFER_SENT (23)`: Original order marked as transfer sent

## Key Functions

- `user_split()`: Creates new order and moves tickets
- `notify_user_split_order_source()`: Sends email to old owner
- `notify_user_split_order_target()`: Sends email to new owner
- `TicketTransferAccept`: View for new owner to accept transfer
- `transfer_needs_accept()`: Checks if confirmation texts are required

## Differences to Intermediated Flow

The **Direct Flow** transfers tickets directly between owners without the ticket shop acting as an intermediary for payment/refund.

The **Intermediated Flow** uses the ticket shop as an intermediary:
- Shop collects payment from new owner
- Shop processes refund to old owner
- Shop mediates the financial transaction

- **No payment required**: Tickets are transferred immediately without payment
- **No refund**: Old owner does not receive a refund
- **Acceptance step**: New owner may need to accept confirmation texts
- **Simpler process**: Only 2-3 steps instead of payment flow
