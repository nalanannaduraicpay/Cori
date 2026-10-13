"""Wait until Oracle accepts connections (first start of a fresh Oracle Free
container takes a few minutes). Connects as SYSTEM using ORACLE_PWD/ORACLE_DSN
from .env, so it works before the Cori users exist.

    python scripts/wait_for_db.py              # wait up to 10 minutes
    python scripts/wait_for_db.py --timeout 60
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cori.db.connection import oracle_system_connection  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=600, help="seconds to wait (default 600)")
    args = ap.parse_args()
    start = time.time()
    while True:
        try:
            with oracle_system_connection() as conn:
                conn.cursor().execute("SELECT 1 FROM DUAL")
            print(f"Oracle is ready ({time.time() - start:.0f}s).")
            return 0
        except Exception as exc:
            waited = time.time() - start
            if waited > args.timeout:
                print(f"\nOracle not ready after {waited:.0f}s: {exc}", file=sys.stderr)
                return 1
            first_line = str(exc).splitlines()[0][:90] if str(exc) else type(exc).__name__
            print(f"  waiting for Oracle ({waited:.0f}s): {first_line}", end="\r", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
