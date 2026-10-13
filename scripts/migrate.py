"""Apply migrations (and optionally seed). Uses the admin user.

    python scripts/migrate.py            # drop + recreate schema
    python scripts/migrate.py --seed     # ... and seed reference data + history
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cori.db.migrate import migrate  # noqa: E402
from cori.db.seed import seed  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    migrate(reset=True)
    if args.seed:
        print(seed())
