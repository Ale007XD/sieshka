### sprint_m1_postgres_wiring
```
Deliverables:
  app/db.py                      — SQLAlchemy async engine + session factory
  app/repositories/order_repo.py — OrderRepository(get_state, write_state) — feeds OrderFSM callbacks
  app/repositories/__init__.py
  alembic.ini + migrations/env.py — wrap 001_initial_schema.sql as proper Alembic migration
  tests/integration/test_order_repo.py — uses pytest-postgresql or testcontainers
constraints:
  - OrderRepository.write_state() is the ONLY method that executes UPDATE orders SET state=...
  - This method becomes the terminal-tool equivalent for M1 (pre-nano-vm)
  - state_reader/state_writer callbacks passed into OrderFSM.__init__ wrap repo methods
gate: pytest GREEN (unit + integration), mypy 0 errors
```