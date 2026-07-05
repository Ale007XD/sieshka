# Sieshka

Restaurant order/kitchen/delivery platform. Business state (orders, kitchen tickets,
inventory, promotions) lives in PostgreSQL behind explicit FSMs. Every state transition
that matters for audit — order approved, payment confirmed, kitchen ticket dispatched —
runs as a governed [`llm-nano-vm`](https://pypi.org/project/llm-nano-vm/) Program, not
as a direct field write.

## Problem

An LLM-assisted order/support agent that can write directly to `order.status` can also
write it wrong, or write it based on a hallucinated read of its own prior output. The
usual fix — validate the LLM's output before trusting it — still leaves a single write
path with no record of what was rejected and why.

## Mechanism

Sieshka splits every business transition into two halves that never touch the same code
path:

```
LLM / agent  → generates a proposed action (text, a classification, a suggested branch)
FSM (nano-vm)→ the ONLY thing allowed to call order_fsm.transition(order_id, event=...)
```

The agent's output is an *input* to a deterministic transition graph, not a command the
system executes on trust. A terminal Tool step either commits the state write or raises —
it never returns a swallowed `"ERROR"` string sentinel that a downstream check might
silently ignore (see [Architectural scars](#architectural-scars) below; this was a real
bug, found and fixed, not a hypothetical one).

```python
# FORBIDDEN anywhere outside a terminal tool:
order.status = "PAID"
fsm.set_state(order_id, "COOKING")

# The only sanctioned path:
order_fsm.transition(order_id, event="PAYMENT_CONFIRMED")
```

CI enforces this with a grep gate (`scripts/check_no_direct_mutation.sh`), not a code
review convention — see [Architecture](docs/ARCHITECTURE.md) §1.2 for the exact check.

## Artifact

Every governed transition produces an `ExecutionReceipt`: `trace_hash` (SHA-256,
recomputable from the stored trace, not stored independently), `final_status`, and a list
of `RejectedTransition` entries — which steps were attempted and refused, and why. This
is not a log line; it's a structure a restaurateur can be shown directly (see
`sprint_m6_receipt_audit_viewer` in [SPRINTS_SIESHKA.md](docs/SPRINTS_SIESHKA.md)) to
answer "why did the system do that" without reading source code.

## Stack

FastAPI (async) + PostgreSQL (business state) + SQLite WAL (nano-vm execution traces,
via `nano-vm-mcp`) + `llm-nano-vm` (transition governance) + OpenRouter/YandexGPT
Pro/GigaChat (LLM, two-hop timeout fallback chain) + Jinja2/HTMX/Alpine (admin dashboard,
no separate JS build). Full breakdown and ADRs: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quickstart

```bash
git clone <this-repo> sieshka && cd sieshka
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # fill in LLM provider keys before enabling M4 agents

docker compose up -d postgres
pytest tests/unit/ -v       # 387 passed, no docker/postgres required — pure in-memory FSM tests
mypy app/ --ignore-missing-imports   # 0 errors
ruff check .                         # clean

uvicorn app.main:app --reload
curl http://localhost:8000/health
```

Integration tests (`tests/integration/`) need Docker (`testcontainers`, spins up a real
Postgres); they skip automatically, not fail, if Docker isn't available:

```bash
pytest tests/integration/ -v -m integration
```

Full deployment instructions, including the current-state gaps below that matter before
exposing this outside localhost: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Current limits

- **No reverse proxy in `docker-compose.yml`.** The app container is the only thing
  between the internet and FastAPI — no nginx, no rate limiting, no TLS termination.
  `/admin/*` and `/admin/ui/*` are auth-gated (argon2 via passlib, HTTP Basic —
  `sprint_m6_auth_gate`, done and independently verified from source), but that auth
  endpoint itself has no brute-force rate limiting yet — add a reverse proxy
  (nginx/Caddy/managed LB) with `limit_req` (or equivalent) before any public
  deployment; this project doesn't ship one.
- **`SQLITE_PATH` default is a literal Windows dev-machine path**
  (`C:/Users/alexd/AppData/Local/Temp/sieshka_nano_vm.db` in `app/config.py`) — harmless
  as a default since `.env` should always override it, but will silently create a
  same-named file relative to cwd on Linux if `.env` is missing SQLITE_PATH. Set it
  explicitly in every deployment's `.env`.
- **WhatsApp/Zalo channels are out of scope** — both require business-account
  verification not pursued for this project; Telegram + Web are the supported channels.
- **`NarrativeReceipt` and `ExecutionReceipt` are deliberately separate** — the former is
  an LLM-generated summary layer on Postgres, the latter is the deterministic,
  recomputable governance artifact. Don't expect Receipt fields to carry narrative text;
  that's a different table by design, not an oversight.

## Architectural scars

Two real bugs, found and fixed, worth knowing before touching `app/tools/`:

**Tool-side error swallowing.** Several DB-writing tools (`order_tools.py`,
`kitchen_tools.py`, `inventory_tools.py`) originally returned the string `"ERROR"` on a
race-guard failure instead of raising. Because none of their calling programs had a
downstream `CONDITION` reading that sentinel, `Trace.status` stayed `SUCCESS` regardless
— a failed write looked identical to a successful one in every trace. Fixed across all
three files: `return "ERROR"` → `raise ValueError(...)`.

**`$output_key.output` vs `$step_id.output`.** Two TOOL steps referenced a prior step's
output using the step's `output_key` alias instead of its `step.id` — nano-vm's
`_resolve()` only indexes by `step.id`. One instance was silent (a free-text DB column
absorbed the literal unresolved string unnoticed); the other was loud (`UUID(literal)`
raised `ValueError` immediately). Both fixed in `app/programs/order_programs.py`.

Full list, with rationale and confidence notes: [DECISIONS.md](docs/DECISIONS.md) (if
present) or the ecosystem-level `DECISIONS.md` entries dated 2026-07-02/03.

## Project layout

```
app/
├── domains/{orders,kitchen,delivery}/   FSMs — business state owner
├── programs/                            nano-vm Program definitions (governed transitions)
├── tools/                               Governed Tools — the only code allowed to write state
├── services/                            OrderService etc. — session boundary, transaction owner
├── api/routes/                          JSON API (/orders, /admin, /kitchen, /delivery)
├── web/                                 HTMX/Jinja2 admin dashboard (/admin/ui/*, M6 DONE, auth-gated)
├── llm/                                 Provider adapters (OpenRouter primary, YandexGPT/GigaChat fallback)
├── telemetry.py                         OTel SDK configuration (M5, done)
└── main.py
docs/
├── ARCHITECTURE.md                      ADRs, invariants, migration gates
├── SPRINTS_SIESHKA.md                   Full sprint backlog — deliverables/constraints/gate per sprint
└── DEPLOYMENT.md                        Local + production deployment, dev-agent workflow
```

## Stack position

nano-vm governs *what the agent does* (state transitions) inside Sieshka. It does not
generate menus, take payments, or manage inventory — those are Sieshka's own FSMs/domain
code. If you're evaluating this as "an FSM framework" or "a food-delivery framework",
that's the wrong frame: the FSM/domain layer is ordinary application code, and nano-vm is
specifically the thin, replaceable layer that makes its transitions replayable and
rejection-traceable.
