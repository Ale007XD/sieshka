CREATE TABLE IF NOT EXISTS max_message_refs (
    entity_kind  VARCHAR(16)  NOT NULL,   -- 'kitchen' | 'order'
    entity_id    VARCHAR(64)  NOT NULL,   -- ticket_id or order_id (as text)
    max_user_id  BIGINT       NOT NULL,   -- recipient's MAX user id
    message_id   VARCHAR(64)  NOT NULL,   -- MAX message mid, for PUT /messages
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (entity_kind, entity_id, max_user_id)
);

COMMENT ON TABLE max_message_refs IS
    'One row per (entity, recipient) MAX chat message — enables edit-in-place
     status cards (2026-08-09) instead of a new message per stage. entity_kind
     distinguishes kitchen-ticket-scoped cards (notify_kitchen_ticket_state,
     notify_admin_kitchen_ticket_state) from order-scoped cards
     (notify_courier_order_state, notify_admin_order_state) — same entity_id
     (e.g. a ticket_id) never collides across kinds. Rows are never explicitly
     deleted on terminal state (HANDED_OFF/DELIVERED/CLOSED) — the table stays
     small (bounded by live tickets/orders x active staff), and a stale row
     pointing at an unreachable/deleted message is handled at the application
     layer: edit_message() returning False falls back to sending a fresh
     message and overwriting the ref (see max_staff_notify.py::_send_or_edit).';
