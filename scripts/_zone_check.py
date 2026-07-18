from nano_vm.validator import ProgramValidator

import app.api.routes.admin as admin_mod
from app.policy.policy_snapshot import (
    ZONE_AGENT_APPLY_POLICY_SNAPSHOT,
    ZONE_AGENT_POLICY_SNAPSHOT,
)
from app.programs.zone_agent_program import PROGRAM_APPLY_ZONE, PROGRAM_COLLECT_ZONE
from app.services.zone_service import ZoneService

assert ProgramValidator(PROGRAM_APPLY_ZONE).validate().is_valid()
assert ProgramValidator(PROGRAM_COLLECT_ZONE).validate().is_valid()
print("imports OK")
print("apply tools:", sorted(ZONE_AGENT_APPLY_POLICY_SNAPSHOT.tool_capabilities.keys()))
print("collect tools:", sorted(ZONE_AGENT_POLICY_SNAPSHOT.tool_capabilities.keys()))
print("admin has zones_admin_ui:", hasattr(admin_mod, "zones_admin_ui"))
print("admin has zone_apply:", hasattr(admin_mod, "zone_apply"))
print("ZoneService has get_by_id:", hasattr(ZoneService, "get_by_id"))
