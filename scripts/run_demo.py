"""End-to-end demo: seed the DB, submit every sample invoice to Cori as one
batch, and print expected vs actual decisions.

    python scripts/run_demo.py                 # in-process (same code path as the API worker)
    python scripts/run_demo.py --api http://localhost:8000   # through the HTTP API
    python scripts/run_demo.py --no-seed       # reuse the current DB (re-registers samples)
"""
from __future__ import annotations

import argparse
import logging
import sys
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cori.config import get_settings  # noqa: E402
from cori.demo import SAMPLES_DIR, compare_outcomes, load_expected, setup  # noqa: E402


def run_in_process(no_seed: bool) -> dict[str, dict]:
    from cori import service
    from cori.samples.generate import generate

    if no_seed:
        expected = generate(SAMPLES_DIR)
        registered = {e["file"]: service.register_upload(SAMPLES_DIR / e["file"], e["file"]) for e in expected}
    else:
        registered = setup()["registered"]
    batch_id = service.submit_batch(list(registered.values()), "run_demo")
    print(f"Batch {batch_id}: {len(registered)} invoices sent to Cori "
          f"(extractors: {', '.join(get_settings().enabled_extractors())}, LLM_PROVIDER={get_settings().llm_provider}, "
          f"DB_BACKEND={get_settings().db_backend})")
    t = time.time()
    service.run_batch(batch_id)
    print(f"Processed in {time.time() - t:.1f}s\n")
    return {f: service.decision_view(i) for f, i in registered.items()}


def run_via_api(base: str, no_seed: bool) -> dict[str, dict]:
    import httpx
    from cori.samples.generate import generate

    if not no_seed:
        setup(register=False)
    else:
        generate(SAMPLES_DIR)
    expected = load_expected()
    with httpx.Client(base_url=base, timeout=60) as c:
        c.get("/health").raise_for_status()
        ids = {}
        for e in expected:
            with open(SAMPLES_DIR / e["file"], "rb") as fh:
                r = c.post("/invoices/upload", files={"file": (e["file"], fh, "application/pdf")})
                r.raise_for_status()
                ids[e["file"]] = r.json()["invoice_id"]
        r = c.post("/batches", json={"invoice_ids": list(ids.values()), "submitted_by": "run_demo"})
        r.raise_for_status()
        batch_id = r.json()["batch_id"]
        print(f"Batch {batch_id} submitted via API")
        while True:
            s = c.get(f"/batches/{batch_id}").json()
            print(f"  {s['status']}: {s['processed']}/{s['invoice_count']}", end="\r")
            if s["status"] in ("COMPLETED", "FAILED", "INTERRUPTED"):
                break
            time.sleep(1)
        print()
        return {f: c.get(f"/invoices/{i}/decision").json() for f, i in ids.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", help="base URL of a running Cori API")
    ap.add_argument("--no-seed", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    actual = run_via_api(args.api, args.no_seed) if args.api else run_in_process(args.no_seed)
    rows = compare_outcomes(load_expected(), actual)

    w = max(len(r["file"]) for r in rows)
    print(f"{'Invoice':<{w}}  {'Expected':<21} {'Actual':<21} OK   Reasons")
    print("-" * (w + 80))
    for r in rows:
        print(f"{r['file']:<{w}}  {r['expected']:<21} {str(r['actual']):<21} {'yes' if r['match'] else 'NO ':<4} "
              f"{', '.join(r['actual_reasons']) or '-'}")
    matched = sum(r["match"] for r in rows)
    print(f"\n{matched}/{len(rows)} invoices reached the expected outcome.")
    print("\nCori's summaries:")
    for r in rows:
        print(f"\n[{r['file']}]")
        print(textwrap.fill(r["summary"] or "", width=110, initial_indent="  ", subsequent_indent="  "))
    return 0 if matched == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
