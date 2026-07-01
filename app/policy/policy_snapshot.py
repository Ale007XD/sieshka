from nano_vm.contracts import PolicySnapshot

ORDERS_TOOL_CAPABILITIES: dict[str, list[str]] = {
    "validate_order_items": ["orders:read"],
    "transition_order_state": ["orders:write"],
    "write_order_state_payment_pending": ["orders:write"],
    "write_order_state_paid": ["orders:write"],
    "write_order_state_payment_failed": ["orders:write"],
    "write_order_state_cooking": ["orders:write"],
    "reserve_inventory_items": ["inventory:write"],
    "create_kitchen_ticket": ["kitchen:write"],
    "log_validation_failure": ["orders:write"],
    "notify_inventory_insufficient": ["notifications:send"],
    "yookassa_create_payment": ["payment:write"],
    "yookassa_verify_payment": ["payment:read"],
}

KITCHEN_TOOL_CAPABILITIES: dict[str, list[str]] = {
    "write_kitchen_state_queued": ["kitchen:write"],
    "write_kitchen_state_preparing": ["kitchen:write"],
    "write_kitchen_state_ready": ["kitchen:write"],
    "write_kitchen_state_handed_off": ["kitchen:write"],
}

DELIVERY_TOOL_CAPABILITIES: dict[str, list[str]] = {}

NOTIFICATION_TOOL_CAPABILITIES: dict[str, list[str]] = {
    "notify_order_confirmed": ["notifications:send"],
    "notify_payment_received": ["notifications:send"],
    "notify_order_cooking": ["notifications:send"],
    "notify_order_delivered": ["notifications:send"],
    "notify_order_failed": ["notifications:send"],
}

INVENTORY_TOOL_CAPABILITIES: dict[str, list[str]] = {
    "check_inventory_stock": ["inventory:read"],
    "decrement_inventory": ["inventory:write"],
    "increment_inventory": ["inventory:write"],
    "set_inventory_state": ["inventory:write"],
}

ORDERS_POLICY_CONFIG: dict[str, object] = {
    "tool_capabilities": ORDERS_TOOL_CAPABILITIES,
}

KITCHEN_POLICY_CONFIG: dict[str, object] = {
    "tool_capabilities": KITCHEN_TOOL_CAPABILITIES,
}

DELIVERY_POLICY_CONFIG: dict[str, object] = {
    "tool_capabilities": DELIVERY_TOOL_CAPABILITIES,
}

ORDERS_POLICY_SNAPSHOT: PolicySnapshot = PolicySnapshot.from_config(
    ORDERS_POLICY_CONFIG,
    policy_id="orders-v1",
    version="1.0.0",
)

KITCHEN_POLICY_SNAPSHOT: PolicySnapshot = PolicySnapshot.from_config(
    KITCHEN_POLICY_CONFIG,
    policy_id="kitchen-v1",
    version="1.0.0",
)

DELIVERY_POLICY_SNAPSHOT: PolicySnapshot = PolicySnapshot.from_config(
    DELIVERY_POLICY_CONFIG,
    policy_id="delivery-v1",
    version="1.0.0",
)
