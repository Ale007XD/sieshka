-- migrations/009_zone_name_unique_index.sql — sprint_m7_zone_agent.
--
-- Hard rule (sprint_m7_zone_agent): a zone NAME must be unique among ACTIVE
-- zones. A retired (is_active = FALSE) zone frees its name for reuse, so the
-- uniqueness is scoped to active rows only via a partial unique index.
--
-- This index is the REAL enforcement mechanism behind apply_zone_command's
-- "name not already in use by an ACTIVE zone" check. validate_zone_command does
-- an earlier hand-rolled SELECT (early rejection only); the genuine race guard
-- is this constraint, hit at write time: a plain INSERT (NOT ON CONFLICT DO
-- UPDATE — zone create must REJECT a collision, not silently overwrite a
-- different zone's row) surfaces a unique_violation that apply_zone_command
-- catches and re-raises as a clear ValueError.
--
-- A single plain UNIQUE(name) is NOT used: it would forbid reusing a name after
-- a zone is soft-deleted, which the sprint explicitly allows (soft delete only;
-- the retired row stays resolvable for past orders' zone_id). The partial index
-- WHERE is_active keeps retired rows out of the uniqueness set.

CREATE UNIQUE INDEX IF NOT EXISTS idx_delivery_zones_name_active
    ON delivery_zones (lower(name)) WHERE is_active;
