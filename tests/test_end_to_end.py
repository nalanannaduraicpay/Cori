"""Full pipeline + HTTP API on a throwaway SQLite database (mock LLM, no
network). Same code path the Oracle deployment uses; only the connection
differs. See test_oracle.py for the Oracle run."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def processed(sqlite_demo):
    from cori import service
    batch_id = service.submit_batch(list(sqlite_demo.values()), "pytest")
    service.run_batch(batch_id)
    return batch_id, {f: service.decision_view(i) for f, i in sqlite_demo.items()}


def test_every_sample_reaches_expected_outcome(processed):
    from cori.demo import compare_outcomes, load_expected
    rows = compare_outcomes(load_expected(), processed[1])
    mismatches = [(r["file"], r["expected"], r["actual"], r["actual_reasons"]) for r in rows if not r["match"]]
    assert not mismatches


def test_batch_completed_with_counts(processed):
    from cori import service
    s = service.batch_status(processed[0])
    assert s["status"] == "COMPLETED" and s["processed"] == s["invoice_count"] == 14
    assert s["decision_counts"] == {"ADVANCED_TO_APPROVAL": 6, "NEEDS_REVIEW": 8}


def test_decision_detail_is_complete(processed):
    v = processed[1]["02_clean_eur_kessler.pdf"]
    assert v["decision"] == "ADVANCED_TO_APPROVAL" and v["summary"].startswith("Advanced:")
    assert v["invoice"]["total"]["display"].endswith(" EUR") and v["invoice"]["total_base"]["display"].endswith(" USD")
    assert v["fx"]["rate"] is not None and v["fx"]["pair"] == "EUR->USD"
    assert len(v["extractions"]) == 2 and all(e["status"] == "ok" for e in v["extractions"])
    assert v["anomaly"]["baseline"]["level"] == "matter" and len(v["anomaly"]["similar_invoices"]) == 5
    assert v["validation_checks"][0]["name"] == "content_safety"


def test_audit_trail_reconstructs_every_step(processed):
    from cori.db import repository as repo
    batch_id, views = processed
    runs = repo.get_agent_runs(views["01_clean_usd_harrington.pdf"]["invoice_id"], batch_id)
    agents = [r["agent_name"] for r in runs]
    for step in ("document_loader", "extractor_a", "extractor_b", "content_guardrail", "consensus_engine",
                 "validation_agent", "anomaly_agent", "cori_router", "cori_summary"):
        assert step in agents
    ex = next(r for r in runs if r["agent_name"] == "extractor_a")
    assert ex["model_name"] and ex["prompt_version"].startswith("extractor_vision.v1#")
    assert ex["raw_output"]["extracted"]["total_amount"]


def test_invoice_statuses_moved(processed):
    from cori.db import repository as repo
    views = processed[1]
    assert repo.get_invoice(views["01_clean_usd_harrington.pdf"]["invoice_id"])["status"] == "ADVANCED_TO_APPROVAL"
    assert repo.get_invoice(views["13_prompt_injection.pdf"]["invoice_id"])["status"] == "NEEDS_REVIEW"


def test_resubmitted_copy_is_caught_as_duplicate(processed):
    from cori import service
    from cori.demo import SAMPLES_DIR
    copy_id = service.register_upload(SAMPLES_DIR / "03_clean_gbp_whitmore.pdf", "copy.pdf")
    service.run_batch(service.submit_batch([copy_id], "pytest"))
    v = service.decision_view(copy_id)
    assert v["decision"] == "NEEDS_REVIEW" and v["reasons"][0]["code"] == "DUPLICATE_INVOICE_NUMBER"
    assert "submitted to Cori earlier" in v["reasons"][0]["message"]


def test_cannot_queue_same_invoice_twice(sqlite_demo):
    from cori import service
    from cori.demo import SAMPLES_DIR
    i = service.register_upload(SAMPLES_DIR / "01_clean_usd_harrington.pdf", "twice.pdf")
    service.submit_batch([i], "pytest")
    with pytest.raises(service.SubmissionError):
        service.submit_batch([i], "pytest")


def test_interrupted_batch_is_released(sqlite_demo):
    from cori import service
    from cori.db import repository as repo
    from cori.demo import SAMPLES_DIR
    i = service.register_upload(SAMPLES_DIR / "01_clean_usd_harrington.pdf", "crash.pdf")
    b = service.submit_batch([i], "pytest")  # never run: simulates a server stop
    assert b in repo.recover_interrupted_batches()
    assert repo.get_batch(b)["status"] == "INTERRUPTED" and repo.get_invoice(i)["status"] == "PENDING"


# ----------------------------------------------------------------------------
# HTTP API (what the C# app will call)
# ----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client(sqlite_demo):
    from fastapi.testclient import TestClient
    from cori.api.main import app
    with TestClient(app) as c:
        yield c


def test_api_health(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["db_backend"] == "sqlite" and "extractor_a" in h["extractors"]


def test_api_send_to_cori_flow(client):
    from cori.demo import SAMPLES_DIR
    with open(SAMPLES_DIR / "07_bank_details_mismatch.pdf", "rb") as fh:
        up = client.post("/invoices/upload", files={"file": ("bank.pdf", fh, "application/pdf")})
    assert up.status_code == 201 and up.json()["status"] == "PENDING"
    inv_id = up.json()["invoice_id"]

    r = client.post("/batches", json={"invoice_ids": [inv_id], "submitted_by": "csharp.app"})
    assert r.status_code == 202  # TestClient runs the background task before returning
    batch = client.get(f"/batches/{r.json()['batch_id']}").json()
    assert batch["status"] == "COMPLETED" and batch["items"][0]["decision"] == "NEEDS_REVIEW"

    d = client.get(f"/invoices/{inv_id}/decision").json()
    codes = [x["code"] for x in d["reasons"]]
    assert "BANK_DETAILS_MISMATCH" in codes

    fb = client.post(f"/invoices/{inv_id}/feedback", json={
        "reviewer": "ap.reviewer", "cleared_flags": ["BANK_DETAILS_MISMATCH"],
        "corrections": [{"field_name": "bank_account", "agent_value": "LV10...", "corrected_value": "DE89..."}]})
    assert fb.status_code == 201 and len(fb.json()["feedback_ids"]) == 2
    assert len(client.get(f"/invoices/{inv_id}/decision").json()["feedback"]) == 2


def test_api_rejects_bad_requests(client):
    assert client.post("/batches", json={"invoice_ids": [], "submitted_by": "x"}).status_code == 422
    r = client.post("/batches", json={"invoice_ids": [123456789], "submitted_by": "x"})
    assert r.status_code == 422 and r.json()["detail"]["invoices"] == {"123456789": "not found"}
    assert client.post("/invoices/upload", files={"file": ("x.pdf", b"not a pdf", "application/pdf")}).status_code == 415
    assert client.get("/invoices/123456789/decision").status_code == 404
    assert client.get("/batches/nope").status_code == 404


def test_api_serves_demo_ui_and_openapi(client):
    assert "Send to Cori" in client.get("/").text
    spec = client.get("/openapi.json").json()
    for path in ("/batches", "/batches/{batch_id}", "/invoices/upload", "/invoices/{invoice_id}/decision",
                 "/invoices/{invoice_id}/feedback", "/health"):
        assert path in spec["paths"]
