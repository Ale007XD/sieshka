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
is not a log line; it's a structure a restaurateur can be shown directly to answer "why
did the system do that" without reading source code.

## Stack

FastAPI (async) + PostgreSQL (business state) + SQLite WAL (nano-vm execution traces,
via `nano-vm-mcp`) + `llm-nano-vm` (transition governance) + OpenRouter/YandexGPT
Pro/GigaChat (LLM, two-hop timeout fallback chain) + Jinja2/HTMX/Alpine (admin dashboard,
no separate JS build) + YooKassa (payments) + MAX Bot API and Zalo Mini App (staff/
storefront channels — see [Channels](#channels) below). Full breakdown and ADRs:
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quickstart

```bash
git clone <this-repo> sieshka && cd sieshka
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # fill in LLM provider keys before enabling M4 agents

docker compose up -d postgres
pytest tests/unit/ -v       # 740 passed, no docker/postgres required — pure in-memory FSM
                             # tests + mocked-service unit tests (MAX/Zalo clients, webhooks)
mypy app/ --ignore-missing-imports   # 0 errors, 122 source files
ruff check .                         # clean

uvicorn app.main:app --reload
curl http://localhost:8000/health
```

Integration tests (`tests/integration/`) need Docker — a live `sieshka-postgres-1`
container (`docker compose up -d postgres`), not `testcontainers`: an earlier version of
this suite used `testcontainers.PostgresContainer`, which hung deterministically on
`initdb` under Docker Desktop/WSL2 on a fresh volume (reproduced manually, not assumed —
see the `postgres_dsn` fixture docstring in `tests/integration/conftest.py`). Each test
creates/drops its own database against the already-running container instead. They skip
automatically, not fail, if Docker isn't available:

```bash
pytest tests/integration/ -v -m integration
```

Full deployment instructions, including the current-state gaps below that matter before
exposing this outside localhost: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Production deploy — the exact commands (2026-10-25, learned the hard way)

Captured after a fresh-VPS deploy crash-looped on
`password authentication failed for user "sieshka"`.

```bash
# THE deploy command — always with the prod overlay. Never bare
# `docker compose up -d` on a server: the base file publishes 5432 to
# 0.0.0.0 with the default password 'sieshka'.
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
```

**The pgdata trap.** `POSTGRES_PASSWORD` only takes effect on the FIRST init
of an empty `pgdata` volume. If the volume already exists (any previous
`up` under the same project directory), changing `.env` does NOT change the
actual role password — the app will crash-loop on exactly the error above.
Fresh install, data disposable:

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml down -v
# then the up command above; verify `docker compose ps` shows NO published 5432
```

Data must be kept? Align the role instead of wiping the volume:

```bash
docker compose exec postgres psql -U postgres \
  -c "ALTER ROLE sieshka WITH PASSWORD '<new POSTGRES_PASSWORD>';"
```

**`.env` must be consistent before the first `up`:** `POSTGRES_PASSWORD` must
equal the password inside `DATABASE_URL` (URL-encode specials there: `@`→`%40`,
`#`→`%23`, `%`→`%25`; a literal `$` in any compose-interpolated value needs
`$$`). A mismatch in either direction produces the same crash-loop.

**Required by the 2026-10-25 security patch (fail-closed by design):**

- `DASHBOARD_PASSWORD_HASH` — without it the admin UI **and** the internal
  JSON APIs (`/orders`, `/kitchen`, `/delivery`) answer 401;
- `MAX_WEBHOOK_SECRET`, `TELEGRAM_WEBHOOK_SECRET` — without them the MAX and
  Telegram webhooks reject every request with 403;
- `YOOKASSA_SHOP_ID` / `YOOKASSA_SECRET_KEY` — the payment webhook now
  re-reads each payment via the YooKassa API before trusting a notification,
  and returns 502 (provider retries) while the API is unreachable.

## Current limits

- **No reverse proxy in `docker-compose.yml`.** The app container is the only thing
  between the internet and FastAPI — no nginx, no rate limiting, no TLS termination.
  Auth state as of the 2026-10-25 security patch: `/admin/*`, `/admin/ui/*` AND the
  internal JSON APIs (`/orders`, `/kitchen`, `/delivery`) are argon2/HTTP-Basic-gated;
  the MAX/Telegram webhook secrets fail closed (403 while unset); `/docs` is
  DEBUG-only. Still missing: brute-force rate limiting on the Basic-auth endpoints
  and on `/webhooks/*` — add a reverse proxy (nginx/Caddy/managed LB) with
  `limit_req` (or equivalent) before any public deployment; this project doesn't
  ship one.
- **Telegram IS a wired channel now** (updated 2026-10-25 — this bullet used to
  claim it was never built, which predated the telegram sprints): bot webhook
  `app/webhooks/telegram.py` (secret-token verified, fail-closed), staff Mini App
  `app/api/routes/telegram_miniapp.py` + `app/web/telegram_routes.py` (same shared
  role ACL as MAX/Zalo via `app/services/staff_dispatch.py`), and storefront
  checkout attribution via `X-Telegram-Init-Data` (same `validate_init_data`
  HMAC as MAX, keyed with `TELEGRAM_BOT_TOKEN`). Deployment needs
  `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` in `.env` — see the
  production-deploy section above. The other real channels — **MAX** (Bot API,
  `app/webhooks/max.py` — staff notifications with inline-keyboard action buttons,
  edit-in-place cards) and **Zalo** (`app/webhooks/zalo_events.py` +
  `app/api/routes/zalo_miniapp.py` — Mini App backend API for staff actions, storefront
  checkout attribution, `user.revoke.consent` webhook; see [Channels](#channels)).
- **WhatsApp is out of scope** — requires business-account verification not pursued for
  this project. **Zalo OA (Official Account) push notifications are deferred**, not
  out of scope outright: Zalo's OA button system (`oa.open.url`/`oa.query.show`) has no
  generic webhook-callback mechanism equivalent to MAX's `inline_keyboard`, and OA itself
  needs a separate business verification (up to ~15 days) on top of the Mini App's own
  registration. Staff on the Zalo channel currently pull (open the Mini App, see pending
  tickets) rather than get pushed a notification — the Mini App backend API itself is
  fully built and channel-agnostic-role-gated the same as MAX, only the "ping staff
  proactively" layer is missing for this one channel.
- **`NarrativeReceipt` and `ExecutionReceipt` are deliberately separate** — the former is
  an LLM-generated summary layer on Postgres, the latter is the deterministic,
  recomputable governance artifact. Don't expect Receipt fields to carry narrative text;
  that's a different table by design, not an oversight.

## Channels

**MAX** (`app/webhooks/max.py`, `app/services/max_client.py`,
`app/services/max_staff_notify.py`) — staff notifications with inline-keyboard action
buttons and edit-in-place cards, role-gated (kitchen/courier/admin/staff — the last is a
2026-08-09 addition, full authority, one combined card). Storefront checkout also accepts
a MAX Mini App `initData` header for order attribution (server-verified, not
client-trusted).

**Zalo** — three layers, split because Zalo's own platforms are split:
- *Mini App backend API* (`app/api/routes/zalo_miniapp.py`, `app/web/zalo_auth.py`) —
  staff open the Mini App (a webview, not a chat bot) and call this API directly to
  transition kitchen tickets/orders; auth is a live per-request call to Zalo's `/me`
  endpoint (`app/services/zalo_client.py`), not an offline-verifiable signature like
  MAX's `initData` — Zalo's access token has no offline verification path. Role gating
  (`app/services/staff_dispatch.py`) is shared with MAX, not duplicated a third time.
- *Storefront attribution* (`app/api/routes/checkout.py`) — mirrors MAX's `client_max_uid`
  pattern exactly: a `client_zalo_uid` column, server-verified per checkout request, never
  trusted from the client body.
- *Events webhook* (`app/webhooks/zalo_events.py`) — exactly one real event,
  `user.revoke.consent` (GDPR-style consent revocation), which anonymizes
  `staff.zalo_user_id`/`orders.client_zalo_uid` for the affected user. **Not** MAX-style
  button-press callbacks — Zalo's Mini App Webhook URL doesn't carry those; see
  [Architectural scars](#architectural-scars).

**Zalo OA push notifications are deferred** (not built) — see
[Current limits](#current-limits) above for why.

**Payments**: YooKassa (RUB card payments, webhook-confirmed) is the only payment
processor wired end-to-end. ZaloPay is not yet integrated.

## Architectural scars

Three real bugs — or in the third case, wrong assumptions caught before they became
bugs — found and fixed, worth knowing before touching `app/tools/` or the Zalo channel:

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

**Zalo integration planning assumed MAX-shaped mechanics that don't exist on Zalo.**
Draft plans for the Zalo channel (pre-implementation) assumed: (1) webhook signatures use
HMAC-SHA256 — the real Zalo formula, confirmed against `docs.zaloplatforms.com`, is plain
`SHA256(sorted-keys-content + api_key)`, no HMAC; (2) the Mini App Webhook URL carries
button-press callbacks like MAX's `message_callback` — it doesn't, a self-developed Mini
App's webhook fires exactly one event, `user.revoke.consent`; button-press-style events
belong to a different Zalo product (OA chat bot) with no direct callback-to-webhook
mechanism at all. Caught by reading the official docs before writing the webhook handler,
not after a production incident — but the draft plans were confident and specific enough
(exact header names, exact JSON shapes) that implementing them without that check would
have shipped a webhook that silently rejected every real event Zalo ever sent it.

Full list, with rationale and confidence notes: the ecosystem-level `DECISIONS.md`
entries dated 2026-07-02/03 (the first two) and 2026-08-13/14 (the third).

## Project layout

```
app/
├── domains/{orders,kitchen,delivery,staff}/  FSMs + staff role/ACL models — business state owner
├── programs/                            nano-vm Program definitions (governed transitions)
├── tools/                               Governed Tools — the only code allowed to write state
├── services/                            OrderService, StaffService, staff_dispatch (shared
│                                         MAX/Zalo role ACL), max_client, zalo_client, etc. —
│                                         session boundary, transaction owner
├── webhooks/                            max.py, zalo_events.py, yookassa.py — inbound
│                                         platform events
├── api/routes/                          JSON API (/orders, /admin, /kitchen, /delivery,
│                                         /api/zalo/* — Mini App staff actions)
├── web/                                 HTMX/Jinja2 admin dashboard (/admin/ui/*, auth-gated),
│                                         zalo_auth.py (Mini App per-request auth dependency)
├── llm/                                 Provider adapters (OpenRouter primary, YandexGPT/GigaChat fallback)
├── telemetry.py                         OTel SDK configuration
└── main.py
docs/
├── ARCHITECTURE.md                      ADRs, invariants, migration gates
├── CONSTRAINTS.md                       Sieshka-specific operating rules
└── DEPLOYMENT.md                        Local + production deployment, dev-agent workflow
```

`docs/ARCHITECTURE.md` and `docs/CONSTRAINTS.md` predate the MAX/Zalo work above and
haven't been updated to reference it yet — this README and the source itself are
currently ahead of those two docs on that specific topic.

## Stack position

nano-vm governs *what the agent does* (state transitions) inside Sieshka. It does not
generate menus, take payments, or manage inventory — those are Sieshka's own FSMs/domain
code. If you're evaluating this as "an FSM framework" or "a food-delivery framework",
that's the wrong frame: the FSM/domain layer is ordinary application code, and nano-vm is
specifically the thin, replaceable layer that makes its transitions replayable and
rejection-traceable.
