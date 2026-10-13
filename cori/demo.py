"""Demo setup helpers used by scripts/setup_demo.py and scripts/run_demo.py."""
from __future__ import annotations

import json
import logging
import re

from cori.config import ROOT, get_settings
from cori.db.migrate import migrate
from cori.db.seed import seed
from cori.samples.generate import generate
from cori.service import register_upload

log = logging.getLogger(__name__)
SAMPLES_DIR = ROOT / "sample_invoices"
_UPLOAD_NAME = re.compile(r"^[0-9a-f]{12}_[A-Za-z0-9._-]+\.pdf$")


def _clear_uploads() -> int:
    """The reset drops every invoice row, so earlier uploads are orphaned.
    Only files named by register_upload (<12 hex>_<name>.pdf) are removed."""
    upload_dir = get_settings().upload_dir
    if not upload_dir.is_dir():
        return 0
    stale = [p for p in upload_dir.iterdir() if p.is_file() and _UPLOAD_NAME.match(p.name)]
    for p in stale:
        p.unlink()
    return len(stale)


def setup(register: bool = True) -> dict:
    """Reset DB, seed history, regenerate sample PDFs, register them as PENDING."""
    migrate(reset=True)
    _clear_uploads()
    stats = seed()
    expected = generate(SAMPLES_DIR)
    registered = {}
    if register:
        for e in expected:
            registered[e["file"]] = register_upload(SAMPLES_DIR / e["file"], e["file"])
    return {"seed": stats, "samples": len(expected), "registered": registered}


def load_expected() -> list[dict]:
    return json.loads((SAMPLES_DIR / "expected_outcomes.json").read_text(encoding="utf-8"))


def compare_outcomes(expected: list[dict], actual_by_file: dict[str, dict]) -> list[dict]:
    rows = []
    for e in expected:
        act = actual_by_file.get(e["file"]) or {}
        codes = [r["code"] for r in act.get("reasons", [])]
        decision_ok = act.get("decision") == e["expected_decision"]
        reasons_ok = set(e["expected_reasons"]).issubset(codes)
        rows.append({"file": e["file"], "expected": e["expected_decision"], "actual": act.get("decision"),
                     "expected_reasons": e["expected_reasons"], "actual_reasons": codes,
                     "match": decision_ok and reasons_ok, "summary": act.get("summary")})
    return rows
