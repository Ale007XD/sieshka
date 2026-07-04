"""Standalone CI script: validate every registered nano-vm Program.

Imports and delegates to app.startup.validate_all_programs() — the
canonical validation logic.  Exits non-zero if any program has
ERROR-severity issues; WARNING-severity issues are non-blocking.
"""

from __future__ import annotations

import sys

from app.startup import validate_all_programs


def main() -> int:
    try:
        validate_all_programs()
    except RuntimeError:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
