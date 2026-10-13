"""Reset the database, seed history, generate sample PDFs and register them
as PENDING invoices so they appear in the demo UI.

    python scripts/setup_demo.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cori.demo import setup  # noqa: E402

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    result = setup(register=True)
    print(json.dumps(result, indent=2))
    print("\nStart the API with:  uvicorn cori.api.main:app --port 8000   then open http://localhost:8000")
