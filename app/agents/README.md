# app/agents — agent Programs and the apply-phase CONVENTION

Agents in this project are **per-domain** (menu_agent, order_agent, and the M7
zone/schedule agents). There is deliberately **no** shared `OpsAgent` base class
and no shared abstract hierarchy — the shape below is a **documented CONVENTION**
that each agent follows independently, exactly the way order_agent/menu_agent
already independently follow the same "collect, not mutate" shape.

## Two-phase design

Every agent that mutates state is split into two phases:

1. **COLLECT** — `collect_input [LLM] → validate_command [TOOL] →
   CONDITION(valid) → confirm_command [TOOL, is_terminal]`. Produces a confirmed
   structured command as terminal JSON. **Writes nothing to Postgres.**

2. **APPLY** — the phase this project's `sprint_m7_agent_apply_phase_pattern`
   introduced. Takes the confirmed command and performs the ONE governed write.

The UI already knows which domain a screen is about (the admin picked the "Menu"
/ "Zones" / "Schedule" tab) before the LLM ever sees the free-text instruction,
so an agent's LLM job is "parse this text into a command for THIS known domain",
never "figure out which domain this is". Keep prompts small and single-domain.

## The APPLY-phase Program shape (the CONVENTION)

```
validate_command [TOOL]
  → CONDITION(valid)
      valid   → apply_command  [TOOL, GovernedToolExecutor-wrapped, is_terminal]
      invalid → report_invalid [TOOL, is_terminal]
```

Reference implementation: **menu_agent** —
`app/programs/menu_agent_program.py::PROGRAM_APPLY_MENU`,
`app/tools/menu_agent_tools.py::apply_menu_command`,
`app/agents/menu_agent.py::MenuAgent.apply_menu`.

### apply_command's defining properties

- **It is the ONLY step in the whole agent Program allowed to write to
  Postgres**, and it does so through the SAME `GovernedToolExecutor.check()`
  capability gate every other Tool goes through (the `order_service._build_vm`
  pattern: closure-injected `session` via `functools.partial`, no independent
  `session_factory()`, **no `commit()` inside the tool** — commit/rollback is
  owned by the calling agent method). See CONSTRAINTS.md
  "Tool-authoring: side-effect session boundary".

- **It MUST raise on any write failure** (constraint violation, target row
  vanished between validate and apply, unique-name collision, …). It is
  `is_terminal` with NO downstream CONDITION reading its output, so a returned
  "ERROR"/0/1 sentinel would leave `Trace.status == SUCCESS` regardless of what
  happened in Postgres. This exact bug class was fixed three times already
  (order_tools, kitchen_tools, inventory_tools) — do not reintroduce it. See
  CONSTRAINTS.md "Terminal TOOL step failure propagation".

- **It RE-VERIFIES at write time (TOCTOU).** `validate_command`'s checks
  (uniqueness, name-not-in-use, target-resolves-to-exactly-one) are an
  early-rejection convenience, **not** the enforcement point. A concurrent
  second agent invocation can interleave between `validate_command` and
  `apply_command`. So `apply_command` re-checks inside its own write
  transaction — a DB-level constraint / `SELECT ... FOR UPDATE` re-check — and
  never trusts the earlier validate alone (same race already fixed in
  order_tools.py, DECISIONS.md 2026-07-01/02). `validate_command` staying a
  numeric-sentinel (0/1) CONDITION-consumer is correct and unaffected.

### Capabilities (per-agent, fail-closed)

`GovernedToolExecutor` denies-by-default, so **every apply_command tool needs
its own capability entry** in `app/policy/policy_snapshot.py`. menu_agent's apply
phase uses `MENU_AGENT_APPLY_TOOL_CAPABILITIES` /
`MENU_AGENT_APPLY_POLICY_SNAPSHOT`. Downstream sprints add their **own** dicts
(e.g. `ZONE_AGENT_APPLY_TOOL_CAPABILITIES`,
`SCHEDULE_AGENT_APPLY_TOOL_CAPABILITIES`) — this is part of the CONVENTION, not a
one-off. Forgetting an entry fails closed at runtime (loud — the executor raises
on an unrecognized tool — not a silent no-op).

## What downstream sprints reuse

`sprint_m7_zone_agent` and `sprint_m7_schedule_agent` are their OWN Programs,
Tools, and policy dicts. They share only this documented CONVENTION, not a base
class. Concrete note carried forward: zone_agent's "name not already in use by an
ACTIVE zone" check has real TOCTOU exposure and MUST be re-verified at write time
(a partial unique index or a `FOR UPDATE` re-check) — a validate-time-only check
is not sufficient.
