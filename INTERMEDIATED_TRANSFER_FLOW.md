# Intermediated Transfer Flow

**Intermediated transfer** with the ticket shop acting as intermediary:
- Shop collects payment from new owner
- Shop processes refund to old owner
- Shop mediates the financial transaction

## Sequence Diagram (simplified)

```mermaid
sequenceDiagram
    participant TO as Ticket Owner<br/>(Original Owner)
    participant TS as Ticket Shop
    participant NO as New Owner

    Note over TO,NO: Phase 1: Transfer Initiation
    
    TO->>TS: 1. Selects tickets
    TO->>TS: 2. Enters new owner's email
    TO->>TS: 3. Enters bank details for refund
    TO->>TS: 4. Confirms transfer
    
    TS->>TS: Creates new order for new owner
    TS->>TS: Moves tickets to new order
    TS->>TS: Stores bank details
    
    TS->>NO: Email: Transfer information<br/>+ Payment link
    TS->>TO: Email: Transfer initiated
    
    Note over TO,NO: Phase 2: Payment by New Owner
    
    NO->>TS: Opens order page (via link)
    TS->>NO: Shows order with payment options
    NO->>TS: Selects payment method
    NO->>TS: Completes payment
    TS->>TS: Payment received and confirmed
    
    Note over TO,NO: Phase 3: Automatic Transfer Completion
    
    TS->>TS: Automatically complete transfer
    TS->>TS: Process refund to original owner<br/>(using stored bank details)
    
    TS->>TO: Email: Transfer completed<br/>+ Refund processed
    TS->>NO: Email: Transfer completed<br/>+ Tickets attached
    
    Note over TO,NO: Transfer successfully completed
```

## Status Values

- `TICKET_TRANSFER_PENDING_PAYMENT (3)`: Transfer initiated, waiting for new owner's payment
- `TICKET_TRANSFER_COMPLETED (4)`: Transfer completed, original owner has been refunded
- `TICKET_TRANSFER_SENT (23)`: Original order marked as transfer sent

## Key Functions

- `initiate_transfer_with_payment()`: Creates new order for new owner
- `complete_transfer_after_payment()`: Completes transfer and processes refund
- `handle_transfer_payment()`: Signal handler for `order_paid` event
