"""Oracle-only checks. Opt in with CORI_TEST_ORACLE=1; uses the Oracle
connection in .env and RESEEDS the Cori schema (CORI_OWNER) - nothing else.

    $env:CORI_TEST_ORACLE=1; pytest -m oracle
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.oracle


@pytest.fixture(scope="module")
def oracle_demo():
    os.environ["DB_BACKEND"] = "oracle"
    from cori.demo import setup
    return setup(register=True)["registered"]


@pytest.mark.parametrize("sql", [
    "UPDATE INVOICES SET TOTAL = 1 WHERE INVOICE_ID = 1",
    "DELETE FROM INVOICES WHERE INVOICE_ID = 1",
    "UPDATE VENDOR_BANK_ACCOUNTS SET IBAN_OR_ACCOUNT = 'X' WHERE ACCOUNT_ID = 1",
    "INSERT INTO VENDORS (VENDOR_ID, NAME, COUNTRY, DEFAULT_CURRENCY) VALUES ('X', 'X', 'US', 'USD')",
    "UPDATE FX_RATES SET RATE = 1",
    "DROP TABLE AGENT_RUNS",
])
def test_agent_user_cannot_change_reference_or_history(oracle_demo, sql):
    import oracledb
    from cori.db.connection import connect
    with pytest.raises(oracledb.DatabaseError, match="ORA-(41900|01031)"):
        with connect("agent") as conn:
            conn.cursor().execute(sql)


def test_embeddings_use_native_vector_type(oracle_demo):
    from cori.db.connection import connect
    with connect("admin") as conn:
        cur = conn.cursor()
        cur.execute("SELECT DATA_TYPE FROM USER_TAB_COLUMNS WHERE TABLE_NAME = 'INVOICE_EMBEDDINGS' "
                    "AND COLUMN_NAME = 'EMBEDDING'")
        assert cur.fetchone()[0] == "VECTOR"


def test_full_pipeline_on_oracle(oracle_demo):
    from cori import service
    from cori.demo import compare_outcomes, load_expected
    batch_id = service.submit_batch(list(oracle_demo.values()), "pytest-oracle")
    service.run_batch(batch_id)
    rows = compare_outcomes(load_expected(), {f: service.decision_view(i) for f, i in oracle_demo.items()})
    assert all(r["match"] for r in rows), [r for r in rows if not r["match"]]
