ALTER TABLE staff DROP CONSTRAINT IF EXISTS staff_role_check;

ALTER TABLE staff
    ADD CONSTRAINT staff_role_check
    CHECK (role IN ('kitchen', 'courier', 'admin', 'staff'));

COMMENT ON CONSTRAINT staff_role_check ON staff IS
    'staff (2026-08-09) = full-authority role — every kitchen event
     (QUEUE/START_PREP/MARK_READY/HAND_OFF) plus every order event courier
     or admin can trigger (ASSIGN_COURIER/PICKUP/DELIVER/CANCEL), offered
     in ONE combined MAX card per order (see max_staff_notify.py::
     _build_staff_card). Not a superset ROLE relationship in code — a
     distinct ACL entry in webhooks/max.py::_KITCHEN_ROLE_EVENTS/
     _ORDER_ROLE_EVENTS, kept in sync manually with kitchen/courier/admin''s
     entries by convention, same as those three already are with each other.';
