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

ORDER_AGENT_TOOL_CAPABILITIES: dict[str, list[str]] = {
    "validate_order_command": ["orders:read"],
    "collect_order_command": ["orders:write"],
    "report_collect_failure": ["orders:read"],
}

MENU_AGENT_TOOL_CAPABILITIES: dict[str, list[str]] = {
    "validate_menu_command": ["menu:read"],
    "collect_menu_command": ["menu:write"],
    "report_collect_failure": ["menu:read"],
}

# Apply phase (sprint_m7_agent_apply_phase_pattern). Its own capability dict —
# every apply_command tool needs its own entry because GovernedToolExecutor
# denies-by-default; forgetting an entry fails closed (loud) at runtime. The
# shared CONVENTION: sprint_m7_zone_agent / sprint_m7_schedule_agent each add
# their OWN dict (ZONE_AGENT_APPLY_TOOL_CAPABILITIES etc.), not a one-off here.
MENU_AGENT_APPLY_TOOL_CAPABILITIES: dict[str, list[str]] = {
    "validate_apply_command": ["menu:read"],
    "apply_menu_command": ["menu:write"],
    "report_invalid_command": ["menu:read"],
}

# Category apply phase — own capability dict per the apply-phase CONVENTION
# (GovernedToolExecutor denies-by-default; a missing entry fails closed).
MENU_AGENT_APPLY_CATEGORY_TOOL_CAPABILITIES: dict[str, list[str]] = {
    "validate_apply_category_command": ["menu:read"],
    "apply_category_command": ["menu:write"],
    "report_invalid_category_command": ["menu:read"],
}

# Product update phase — own capability dict per the apply-phase CONVENTION.
MENU_AGENT_UPDATE_PRODUCT_TOOL_CAPABILITIES: dict[str, list[str]] = {
    "validate_update_product_command": ["menu:read"],
    "apply_update_product_command": ["menu:write"],
    "report_invalid_update_product_command": ["menu:read"],
}

# schedule_m7_schedule_agent — its OWN capability dict, per the apply-phase
# CONVENTION (every apply_command needs its own entry; GovernedToolExecutor
# denies-by-default, so a missing entry fails closed at runtime).
SCHEDULE_AGENT_TOOL_CAPABILITIES: dict[str, list[str]] = {
    "validate_schedule_command": ["schedule:read"],
    "collect_schedule_command": ["schedule:write"],
    "report_collect_failure": ["schedule:read"],
}

SCHEDULE_AGENT_APPLY_TOOL_CAPABILITIES: dict[str, list[str]] = {
    "validate_apply_schedule_command": ["schedule:read"],
    "apply_schedule_command": ["schedule:write"],
    "report_invalid_schedule_command": ["schedule:read"],
}

# sprint_m7_zone_agent — its OWN capability dicts, per the apply-phase CONVENTION
# (every agent adds its own collect + apply dicts; GovernedToolExecutor
# denies-by-default, so a missing entry fails closed at runtime). zone_agent
# lives under the "delivery:" capability family, matching DeliveryZone's
# app/domains/delivery namespace.
ZONE_AGENT_TOOL_CAPABILITIES: dict[str, list[str]] = {
    "validate_zone_command": ["delivery:read"],
    "collect_zone_command": ["delivery:write"],
    "report_collect_failure": ["delivery:read"],
}

ZONE_AGENT_APPLY_TOOL_CAPABILITIES: dict[str, list[str]] = {
    "validate_apply_zone_command": ["delivery:read"],
    "apply_zone_command": ["delivery:write"],
    "report_invalid_zone_command": ["delivery:read"],
}

PROMOTION_AGENT_TOOL_CAPABILITIES: dict[str, list[str]] = {
    "validate_promotion_command": ["promotion:read"],
    "collect_promotion_command": ["promotion:write"],
    "report_collect_failure": ["promotion:read"],
}

SUPPORT_AGENT_TOOL_CAPABILITIES: dict[str, list[str]] = {
    "validate_support_command": ["support:read"],
    "collect_support_command": ["support:write"],
    "report_collect_failure": ["support:read"],
}

ORDER_AGENT_POLICY_CONFIG: dict[str, object] = {
    "tool_capabilities": ORDER_AGENT_TOOL_CAPABILITIES,
}

MENU_AGENT_POLICY_CONFIG: dict[str, object] = {
    "tool_capabilities": MENU_AGENT_TOOL_CAPABILITIES,
}

MENU_AGENT_APPLY_POLICY_CONFIG: dict[str, object] = {
    "tool_capabilities": MENU_AGENT_APPLY_TOOL_CAPABILITIES,
}

MENU_AGENT_APPLY_CATEGORY_POLICY_CONFIG: dict[str, object] = {
    "tool_capabilities": MENU_AGENT_APPLY_CATEGORY_TOOL_CAPABILITIES,
}

MENU_AGENT_UPDATE_PRODUCT_POLICY_CONFIG: dict[str, object] = {
    "tool_capabilities": MENU_AGENT_UPDATE_PRODUCT_TOOL_CAPABILITIES,
}

SCHEDULE_AGENT_POLICY_CONFIG: dict[str, object] = {
    "tool_capabilities": SCHEDULE_AGENT_TOOL_CAPABILITIES,
}

SCHEDULE_AGENT_APPLY_POLICY_CONFIG: dict[str, object] = {
    "tool_capabilities": SCHEDULE_AGENT_APPLY_TOOL_CAPABILITIES,
}

PROMOTION_AGENT_POLICY_CONFIG: dict[str, object] = {
    "tool_capabilities": PROMOTION_AGENT_TOOL_CAPABILITIES,
}

SUPPORT_AGENT_POLICY_CONFIG: dict[str, object] = {
    "tool_capabilities": SUPPORT_AGENT_TOOL_CAPABILITIES,
}

ORDER_AGENT_POLICY_SNAPSHOT: PolicySnapshot = PolicySnapshot.from_config(
    ORDER_AGENT_POLICY_CONFIG,
    policy_id="order-agent-v1",
    version="1.0.0",
)

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

MENU_AGENT_POLICY_SNAPSHOT: PolicySnapshot = PolicySnapshot.from_config(
    MENU_AGENT_POLICY_CONFIG,
    policy_id="menu-agent-v1",
    version="1.0.0",
)

MENU_AGENT_APPLY_POLICY_SNAPSHOT: PolicySnapshot = PolicySnapshot.from_config(
    MENU_AGENT_APPLY_POLICY_CONFIG,
    policy_id="menu-agent-apply-v1",
    version="1.0.0",
)

MENU_AGENT_APPLY_CATEGORY_POLICY_SNAPSHOT: PolicySnapshot = PolicySnapshot.from_config(
    MENU_AGENT_APPLY_CATEGORY_POLICY_CONFIG,
    policy_id="menu-agent-apply-category-v1",
    version="1.0.0",
)

MENU_AGENT_UPDATE_PRODUCT_POLICY_SNAPSHOT: PolicySnapshot = PolicySnapshot.from_config(
    MENU_AGENT_UPDATE_PRODUCT_POLICY_CONFIG,
    policy_id="menu-agent-update-product-v1",
    version="1.0.0",
)

SCHEDULE_AGENT_POLICY_SNAPSHOT: PolicySnapshot = PolicySnapshot.from_config(
    SCHEDULE_AGENT_POLICY_CONFIG,
    policy_id="schedule-agent-v1",
    version="1.0.0",
)

SCHEDULE_AGENT_APPLY_POLICY_SNAPSHOT: PolicySnapshot = PolicySnapshot.from_config(
    SCHEDULE_AGENT_APPLY_POLICY_CONFIG,
    policy_id="schedule-agent-apply-v1",
    version="1.0.0",
)

ZONE_AGENT_POLICY_CONFIG: dict[str, object] = {
    "tool_capabilities": ZONE_AGENT_TOOL_CAPABILITIES,
}

ZONE_AGENT_APPLY_POLICY_CONFIG: dict[str, object] = {
    "tool_capabilities": ZONE_AGENT_APPLY_TOOL_CAPABILITIES,
}

ZONE_AGENT_POLICY_SNAPSHOT: PolicySnapshot = PolicySnapshot.from_config(
    ZONE_AGENT_POLICY_CONFIG,
    policy_id="zone-agent-v1",
    version="1.0.0",
)

ZONE_AGENT_APPLY_POLICY_SNAPSHOT: PolicySnapshot = PolicySnapshot.from_config(
    ZONE_AGENT_APPLY_POLICY_CONFIG,
    policy_id="zone-agent-apply-v1",
    version="1.0.0",
)

PROMOTION_AGENT_POLICY_SNAPSHOT: PolicySnapshot = PolicySnapshot.from_config(
    PROMOTION_AGENT_POLICY_CONFIG,
    policy_id="promotion-agent-v1",
    version="1.0.0",
)

SUPPORT_AGENT_POLICY_SNAPSHOT: PolicySnapshot = PolicySnapshot.from_config(
    SUPPORT_AGENT_POLICY_CONFIG,
    policy_id="support-agent-v1",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# Menu CSV import (sprint_m7_menu_csv_import) — governed write, no LLM step.
# The single terminal tool (apply_menu_import) mutates product rows and must
# carry the menu:write capability, identical discipline to every other write.
# ---------------------------------------------------------------------------

MENU_IMPORT_TOOL_CAPABILITIES: dict[str, list[str]] = {
    "apply_menu_import": ["menu:write"],
}

MENU_IMPORT_POLICY_CONFIG: dict[str, object] = {
    "tool_capabilities": MENU_IMPORT_TOOL_CAPABILITIES,
}

MENU_IMPORT_POLICY_SNAPSHOT: PolicySnapshot = PolicySnapshot.from_config(
    MENU_IMPORT_POLICY_CONFIG,
    policy_id="menu-import-v1",
    version="1.0.0",
)

