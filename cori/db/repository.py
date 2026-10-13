"""All database access for Cori. Agents never write SQL; they call these
parameterized functions. Table/column names come from ``mapping.py``.

Every function opens its own short connection (agent role unless stated), so
functions are safe to call from concurrent graph branches.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from cori.db import mapping as M
from cori.db.connection import connect, is_oracle, limit, vector_bind, vector_distance

_id_lock = threading.Lock()

_DATE_COLS = {"invoice_date", "rate_date", "fx_rate_date"}
_TS_COLS = {"created_at", "submitted_at", "finished_at", "updated_at", "started_at", "decided_at", "reviewed_at"}
_DEC_COLS = {"subtotal", "tax", "total", "total_usd", "amount", "unit_price", "quantity", "rate", "fx_rate_used"}
_INT_COLS = {"invoice_id", "matter_id", "client_id", "minor_units", "invoice_count", "is_active",
             "line_id", "account_id", "cnt"}


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def json_default(o: Any):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(f"not JSON serializable: {type(o)}")


def to_json(obj: Any) -> str:
    return json.dumps(obj, default=json_default, ensure_ascii=False)


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _bind_value(v: Any) -> Any:
    if is_oracle():
        return v
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    return v


def _binds(params: dict[str, Any] | None) -> dict[str, Any]:
    return {k: _bind_value(v) for k, v in (params or {}).items()}


def _coerce(row: dict[str, Any]) -> dict[str, Any]:
    for k, v in row.items():
        if v is None:
            continue
        if k in _DATE_COLS:
            row[k] = v.date() if isinstance(v, datetime) else (v if isinstance(v, date) else date.fromisoformat(str(v)[:10]))
        elif k in _TS_COLS:
            row[k] = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
        elif k in _DEC_COLS:
            row[k] = v if isinstance(v, Decimal) else Decimal(str(v))
        elif k in _INT_COLS:
            row[k] = int(v)
        elif k == "distance":
            row[k] = float(v)
    return row


def _fetchall(sql: str, params: dict[str, Any] | None = None, role: str = "agent") -> list[dict[str, Any]]:
    with connect(role) as conn:
        cur = conn.cursor()
        cur.execute(sql, _binds(params))
        cols = [d[0].lower() for d in cur.description]
        return [_coerce(dict(zip(cols, r))) for r in cur.fetchall()]


def _fetchone(sql: str, params: dict[str, Any] | None = None, role: str = "agent") -> dict[str, Any] | None:
    rows = _fetchall(sql, params, role)
    return rows[0] if rows else None


def _execute(sql: str, params: dict[str, Any] | None = None, role: str = "agent") -> int:
    with connect(role) as conn:
        cur = conn.cursor()
        cur.execute(sql, _binds(params))
        return cur.rowcount


def _insert_sql(table: M.Table, logical_cols: Iterable[str]) -> str:
    cols = list(logical_cols)
    return (f"INSERT INTO {table} ({', '.join(table.col(c) for c in cols)}) "
            f"VALUES ({', '.join(':' + c for c in cols)})")


def _insert(table: M.Table, row: dict[str, Any], role: str = "agent") -> None:
    _execute(_insert_sql(table, row.keys()), row, role)


# ----------------------------------------------------------------------------
# reference data
# ----------------------------------------------------------------------------
def get_currency(code: str) -> dict | None:
    C = M.CURRENCIES
    return _fetchone(f"SELECT {C.select()} FROM {C} WHERE {C.currency_code} = :code", {"code": code.upper()})


def list_currencies() -> list[dict]:
    C = M.CURRENCIES
    return _fetchall(f"SELECT {C.select()} FROM {C} ORDER BY {C.currency_code}")


def get_fx_rate_to_usd(currency: str, on_date: date) -> tuple[Decimal, date] | None:
    """Rate currency->USD on ``on_date`` or the nearest prior date."""
    F = M.FX_RATES
    row = _fetchone(
        f"SELECT {F.select('rate', 'rate_date')} FROM {F} "
        f"WHERE {F.from_currency} = :ccy AND {F.to_currency} = 'USD' AND {F.rate_date} <= :d "
        f"ORDER BY {F.rate_date} DESC" + limit(1),
        {"ccy": currency.upper(), "d": on_date})
    return (row["rate"], row["rate_date"]) if row else None


# ----------------------------------------------------------------------------
# vendors / clients / matters
# ----------------------------------------------------------------------------
def list_vendors() -> list[dict]:
    V = M.VENDORS
    return _fetchall(f"SELECT {V.select()} FROM {V} ORDER BY {V.vendor_id}")


def get_vendor(vendor_id: str) -> dict | None:
    V = M.VENDORS
    return _fetchone(f"SELECT {V.select()} FROM {V} WHERE {V.vendor_id} = :vid", {"vid": vendor_id})


def get_vendor_currencies(vendor_id: str) -> list[str]:
    VC = M.VENDOR_CURRENCIES
    rows = _fetchall(f"SELECT {VC.select('currency_code')} FROM {VC} WHERE {VC.vendor_id} = :vid",
                     {"vid": vendor_id})
    return [r["currency_code"] for r in rows]


def get_vendor_bank_accounts(vendor_id: str, active_only: bool = True) -> list[dict]:
    B = M.VENDOR_BANK_ACCOUNTS
    sql = f"SELECT {B.select()} FROM {B} WHERE {B.vendor_id} = :vid"
    if active_only:
        sql += f" AND {B.is_active} = 1"
    return _fetchall(sql, {"vid": vendor_id})


def get_client_by_number(client_number: str) -> dict | None:
    C = M.CLIENTS
    return _fetchone(f"SELECT {C.select()} FROM {C} WHERE UPPER({C.client_number}) = :n",
                     {"n": client_number.strip().upper()})


def get_client(client_id: int) -> dict | None:
    C = M.CLIENTS
    return _fetchone(f"SELECT {C.select()} FROM {C} WHERE {C.client_id} = :id", {"id": client_id})


def get_matter_by_number(matter_number: str) -> dict | None:
    Mt = M.MATTERS
    return _fetchone(f"SELECT {Mt.select()} FROM {Mt} WHERE UPPER({Mt.matter_number}) = :n",
                     {"n": matter_number.strip().upper()})


# ----------------------------------------------------------------------------
# invoices
# ----------------------------------------------------------------------------
def get_invoice(invoice_id: int) -> dict | None:
    I = M.INVOICES
    return _fetchone(f"SELECT {I.select()} FROM {I} WHERE {I.invoice_id} = :id", {"id": invoice_id})


def list_invoices(statuses: list[str] | None = None, exclude_source: str | None = "HISTORICAL",
                  max_rows: int = 500) -> list[dict]:
    I = M.INVOICES
    where, params = [], {}
    if statuses:
        names = []
        for i, s in enumerate(statuses):
            params[f"s{i}"] = s
            names.append(f":s{i}")
        where.append(f"{I.status} IN ({', '.join(names)})")
    if exclude_source:
        where.append(f"{I.source} <> :src")
        params["src"] = exclude_source
    sql = f"SELECT {I.select()} FROM {I}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {I.invoice_id}" + limit(max_rows)
    return _fetchall(sql, params)


def _next_id(table: M.Table, id_logical: str, role: str = "agent") -> int:
    row = _fetchone(f"SELECT COALESCE(MAX({table.col(id_logical)}), 0) + 1 AS cnt FROM {table}", role=role)
    return int(row["cnt"])


def register_pending_invoice(file_path: str, original_filename: str) -> int:
    """Register an uploaded PDF as a PENDING invoice (demo upload flow)."""
    with _id_lock:
        new_id = max(_next_id(M.INVOICES, "invoice_id"), 900001)
        _insert(M.INVOICES, {"invoice_id": new_id, "status": M.STATUS_PENDING, "source": "UPLOAD",
                             "file_path": file_path, "original_filename": original_filename,
                             "created_at": now()})
    return new_id


def update_invoice_status(invoice_id: int, status: str) -> None:
    I = M.INVOICES
    _execute(f"UPDATE {I} SET {I.status} = :st WHERE {I.invoice_id} = :id", {"st": status, "id": invoice_id})


def _duplicate_candidates_sql(where_inv: str, where_ext: str) -> str:
    """Invoices in IAS history plus invoices Cori already processed (their
    agreed headers live in AGENT_INVOICE_EXTRACTS, since uploads carry no
    header data in INVOICES). ``origin`` tells the two apart."""
    I, X = M.INVOICES, M.AGENT_INVOICE_EXTRACTS
    return (f"SELECT {I.select('invoice_id', 'invoice_number', 'invoice_date', 'currency', 'total', 'status')}, "
            f"'IAS' AS origin FROM {I} WHERE {where_inv} "
            f"UNION ALL "
            f"SELECT {X.select('invoice_id', 'invoice_number', 'invoice_date', 'currency', 'total')}, "
            f"{X.decision} AS status, 'CORI' AS origin FROM {X} WHERE {where_ext}")


def find_invoices_by_vendor_number(vendor_id: str, invoice_number: str, exclude_invoice_id: int) -> list[dict]:
    I, X = M.INVOICES, M.AGENT_INVOICE_EXTRACTS
    sql = _duplicate_candidates_sql(
        f"{I.vendor_id} = :vid AND UPPER({I.invoice_number}) = :num AND {I.invoice_id} <> :ex",
        f"{X.vendor_id} = :vid AND UPPER({X.invoice_number}) = :num AND {X.invoice_id} <> :ex")
    return _fetchall(sql, {"vid": vendor_id, "num": invoice_number.strip().upper(), "ex": exclude_invoice_id})


def find_invoices_by_vendor_in_window(vendor_id: str, date_from: date, date_to: date,
                                      exclude_invoice_id: int) -> list[dict]:
    I, X = M.INVOICES, M.AGENT_INVOICE_EXTRACTS
    sql = _duplicate_candidates_sql(
        f"{I.vendor_id} = :vid AND {I.invoice_date} BETWEEN :d1 AND :d2 AND {I.invoice_id} <> :ex",
        f"{X.vendor_id} = :vid AND {X.invoice_date} BETWEEN :d1 AND :d2 AND {X.invoice_id} <> :ex")
    return _fetchall(sql, {"vid": vendor_id, "d1": date_from, "d2": date_to, "ex": exclude_invoice_id})


def upsert_invoice_extract(*, invoice_id: int, batch_id: str, vendor_id: str | None, matter_id: int | None,
                           invoice_number: str | None, invoice_date: date | None, currency: str | None,
                           total: Decimal | None, total_usd: Decimal | None, decision: str) -> None:
    X = M.AGENT_INVOICE_EXTRACTS
    row = {"invoice_id": invoice_id, "batch_id": batch_id, "vendor_id": vendor_id, "matter_id": matter_id,
           "invoice_number": invoice_number, "invoice_date": invoice_date, "currency": currency, "total": total,
           "total_usd": total_usd, "decision": decision, "updated_at": now()}
    sets = ", ".join(f"{X.col(c)} = :{c}" for c in row if c != "invoice_id")
    with connect("agent") as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE {X} SET {sets} WHERE {X.invoice_id} = :invoice_id", _binds(row))
        if cur.rowcount == 0:
            cur.execute(_insert_sql(X, row.keys()), _binds(row))


def get_historical_totals(level: str, key: Any, exclude_invoice_id: int) -> list[dict]:
    """Approved historical invoice totals at a baseline level:
    ``matter`` (matter_id), ``client`` (client_id), ``vendor`` (vendor_id) or ``global``."""
    I, Mt = M.INVOICES, M.MATTERS
    cols = I.select("invoice_id", "vendor_id", "matter_id", "invoice_date", "currency", "total", "total_usd",
                    alias="i")
    sql = f"SELECT {cols} FROM {I} i"
    params: dict[str, Any] = {"st": M.STATUS_HISTORICAL, "ex": exclude_invoice_id}
    if level == "matter":
        sql += f" WHERE i.{I.matter_id} = :k"
        params["k"] = key
    elif level == "client":
        sql += f" JOIN {Mt} m ON m.{Mt.matter_id} = i.{I.matter_id} WHERE m.{Mt.client_id} = :k"
        params["k"] = key
    elif level == "vendor":
        sql += f" WHERE i.{I.vendor_id} = :k"
        params["k"] = key
    elif level == "global":
        sql += " WHERE 1 = 1"
    else:
        raise ValueError(level)
    sql += f" AND i.{I.status} = :st AND i.{I.invoice_id} <> :ex AND i.{I.total_usd} IS NOT NULL"
    return _fetchall(sql, params)


def get_line_item_history(matter_id: int) -> list[dict]:
    """Historical line items for a matter with their invoice's FX context, so
    unit prices can be compared in the base currency."""
    I, L = M.INVOICES, M.INVOICE_LINE_ITEMS
    return _fetchall(
        f"SELECT {L.select('timekeeper', 'description', 'quantity', 'unit_price', 'amount', alias='l')}, "
        f"{I.select('invoice_id', 'currency', 'total', 'total_usd', 'invoice_date', alias='i')} "
        f"FROM {L} l JOIN {I} i ON i.{I.invoice_id} = l.{L.invoice_id} "
        f"WHERE i.{I.matter_id} = :mid AND i.{I.status} = :st",
        {"mid": matter_id, "st": M.STATUS_HISTORICAL})


def get_vendor_matter_invoice_dates(vendor_id: str, matter_id: int, exclude_invoice_id: int) -> list[date]:
    I = M.INVOICES
    rows = _fetchall(
        f"SELECT {I.select('invoice_date')} FROM {I} WHERE {I.vendor_id} = :vid AND {I.matter_id} = :mid "
        f"AND {I.status} = :st AND {I.invoice_id} <> :ex AND {I.invoice_date} IS NOT NULL",
        {"vid": vendor_id, "mid": matter_id, "st": M.STATUS_HISTORICAL, "ex": exclude_invoice_id})
    return [r["invoice_date"] for r in rows]


# ----------------------------------------------------------------------------
# embeddings / vector search
# ----------------------------------------------------------------------------
def upsert_invoice_embedding(invoice_id: int, vector: list[float], model_name: str) -> None:
    E = M.INVOICE_EMBEDDINGS
    params = {"id": invoice_id, "emb": vector_bind(vector), "model": model_name, "ts": now()}
    with connect("agent") as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE {E} SET {E.embedding} = :emb, {E.model_name} = :model, {E.created_at} = :ts "
                    f"WHERE {E.invoice_id} = :id", _binds(params))
        if cur.rowcount == 0:
            cur.execute(_insert_sql(E, ["invoice_id", "embedding", "model_name", "created_at"]),
                        _binds({"invoice_id": invoice_id, "embedding": params["emb"],
                                "model_name": model_name, "created_at": params["ts"]}))


def find_similar_invoices(vector: list[float], level: str, key: Any, exclude_invoice_id: int,
                          k: int = 5) -> list[dict]:
    """k nearest historical invoices by cosine VECTOR_DISTANCE within a matter/client/vendor."""
    E, I, V, Mt = M.INVOICE_EMBEDDINGS, M.INVOICES, M.VENDORS, M.MATTERS
    dist = vector_distance(f"e.{E.embedding}", "qv")
    sql = (f"SELECT {I.select('invoice_id', 'invoice_number', 'invoice_date', 'currency', 'total', 'total_usd', alias='i')}, "
           f"v.{V.name} AS vendor_name, {dist} AS distance "
           f"FROM {E} e JOIN {I} i ON i.{I.invoice_id} = e.{E.invoice_id} "
           f"LEFT JOIN {V} v ON v.{V.vendor_id} = i.{I.vendor_id} ")
    params: dict[str, Any] = {"qv": vector_bind(vector), "st": M.STATUS_HISTORICAL, "ex": exclude_invoice_id}
    if level == "matter":
        sql += f"WHERE i.{I.matter_id} = :k "
        params["k"] = key
    elif level == "client":
        sql += f"JOIN {Mt} m ON m.{Mt.matter_id} = i.{I.matter_id} WHERE m.{Mt.client_id} = :k "
        params["k"] = key
    elif level == "vendor":
        sql += f"WHERE i.{I.vendor_id} = :k "
        params["k"] = key
    else:
        sql += "WHERE 1 = 1 "
    sql += f"AND i.{I.status} = :st AND i.{I.invoice_id} <> :ex ORDER BY distance" + limit(k)
    return _fetchall(sql, params)


# ----------------------------------------------------------------------------
# batches / audit trail
# ----------------------------------------------------------------------------
def create_batch(batch_id: str, submitted_by: str, invoice_ids: list[int]) -> None:
    ts = now()
    with connect("agent") as conn:
        cur = conn.cursor()
        cur.execute(_insert_sql(M.AGENT_BATCHES, ["batch_id", "submitted_by", "submitted_at", "status", "invoice_count"]),
                    _binds({"batch_id": batch_id, "submitted_by": submitted_by, "submitted_at": ts,
                            "status": "QUEUED", "invoice_count": len(invoice_ids)}))
        cur.executemany(_insert_sql(M.AGENT_BATCH_ITEMS, ["batch_id", "invoice_id", "status", "current_step", "updated_at"]),
                        [_binds({"batch_id": batch_id, "invoice_id": i, "status": "QUEUED",
                                 "current_step": None, "updated_at": ts}) for i in invoice_ids])


def set_batch_status(batch_id: str, status: str, finished: bool = False) -> None:
    B = M.AGENT_BATCHES
    if finished:
        _execute(f"UPDATE {B} SET {B.status} = :st, {B.finished_at} = :ts WHERE {B.batch_id} = :id",
                 {"st": status, "ts": now(), "id": batch_id})
    else:
        _execute(f"UPDATE {B} SET {B.status} = :st WHERE {B.batch_id} = :id", {"st": status, "id": batch_id})


def update_batch_item(batch_id: str, invoice_id: int, status: str | None = None, step: str | None = None) -> None:
    BI = M.AGENT_BATCH_ITEMS
    sets, params = [f"{BI.updated_at} = :ts"], {"ts": now(), "b": batch_id, "i": invoice_id}
    if status:
        sets.append(f"{BI.status} = :st")
        params["st"] = status
    if step:
        sets.append(f"{BI.current_step} = :step")
        params["step"] = step
    _execute(f"UPDATE {BI} SET {', '.join(sets)} WHERE {BI.batch_id} = :b AND {BI.invoice_id} = :i", params)


def get_batch(batch_id: str) -> dict | None:
    B = M.AGENT_BATCHES
    return _fetchone(f"SELECT {B.select()} FROM {B} WHERE {B.batch_id} = :id", {"id": batch_id})


def get_batch_items(batch_id: str) -> list[dict]:
    BI = M.AGENT_BATCH_ITEMS
    return _fetchall(f"SELECT {BI.select()} FROM {BI} WHERE {BI.batch_id} = :id ORDER BY {BI.invoice_id}",
                     {"id": batch_id})


def close_unfinished_batch(batch_id: str, batch_status: str) -> None:
    """Mark the batch finished with ``batch_status``, its undecided items
    ERROR, and put those invoices back to PENDING so they can be resent."""
    BI, I = M.AGENT_BATCH_ITEMS, M.INVOICES
    for it in _fetchall(f"SELECT {BI.select('invoice_id')} FROM {BI} WHERE {BI.batch_id} = :b "
                        f"AND {BI.status} NOT IN ('DONE', 'ERROR')", {"b": batch_id}):
        _execute(f"UPDATE {I} SET {I.status} = :st WHERE {I.invoice_id} = :id AND {I.status} = :cur",
                 {"st": M.STATUS_PENDING, "id": it["invoice_id"], "cur": M.STATUS_IN_PROGRESS})
        update_batch_item(batch_id, it["invoice_id"], status="ERROR", step=batch_status.lower())
    set_batch_status(batch_id, batch_status, finished=True)


def recover_interrupted_batches() -> list[str]:
    """Batches left QUEUED/RUNNING by a stopped server (background work is
    in-process, so it does not survive a restart) become INTERRUPTED."""
    B = M.AGENT_BATCHES
    stuck = _fetchall(f"SELECT {B.select('batch_id')} FROM {B} WHERE {B.status} IN ('QUEUED', 'RUNNING')")
    for b in stuck:
        close_unfinished_batch(b["batch_id"], "INTERRUPTED")
    return [b["batch_id"] for b in stuck]


def list_batches(max_rows: int = 20) -> list[dict]:
    B = M.AGENT_BATCHES
    return _fetchall(f"SELECT {B.select()} FROM {B} ORDER BY {B.submitted_at} DESC" + limit(max_rows))


def log_agent_run(*, batch_id: str | None, invoice_id: int | None, agent_name: str, model_name: str | None,
                  prompt_version: str | None, raw_output: Any, started_at: datetime,
                  finished_at: datetime | None, status: str, error: str | None = None) -> str:
    run_id = str(uuid.uuid4())
    _insert(M.AGENT_RUNS, {
        "run_id": run_id, "batch_id": batch_id, "invoice_id": invoice_id, "agent_name": agent_name,
        "model_name": model_name, "prompt_version": prompt_version,
        "raw_output": to_json(raw_output if raw_output is not None else {}),
        "started_at": started_at, "finished_at": finished_at or now(), "status": status,
        "error_message": (error or "")[:3900] or None,
    })
    return run_id


def get_agent_runs(invoice_id: int, batch_id: str | None = None) -> list[dict]:
    R = M.AGENT_RUNS
    sql = f"SELECT {R.select()} FROM {R} WHERE {R.invoice_id} = :id"
    params: dict[str, Any] = {"id": invoice_id}
    if batch_id:
        sql += f" AND {R.batch_id} = :b"
        params["b"] = batch_id
    rows = _fetchall(sql + f" ORDER BY {R.started_at}", params)
    for r in rows:
        r["raw_output"] = json.loads(r["raw_output"]) if r.get("raw_output") else None
    return rows


def insert_decision(*, invoice_id: int, batch_id: str, decision: str, summary: str, reasons: Any,
                    consensus: Any, flags: Any, detail: Any, fx_rate_used: Decimal | None,
                    fx_rate_date: date | None) -> str:
    decision_id = str(uuid.uuid4())
    _insert(M.AGENT_DECISIONS, {
        "decision_id": decision_id, "invoice_id": invoice_id, "batch_id": batch_id, "decision": decision,
        "summary": summary, "reasons": to_json(reasons), "consensus": to_json(consensus),
        "flags": to_json(flags), "detail": to_json(detail), "fx_rate_used": fx_rate_used,
        "fx_rate_date": fx_rate_date, "decided_at": now(),
    })
    return decision_id


def get_latest_decision(invoice_id: int) -> dict | None:
    D = M.AGENT_DECISIONS
    row = _fetchone(f"SELECT {D.select()} FROM {D} WHERE {D.invoice_id} = :id ORDER BY {D.decided_at} DESC" + limit(1),
                    {"id": invoice_id})
    if row:
        for k in ("reasons", "consensus", "flags", "detail"):
            row[k] = json.loads(row[k]) if row.get(k) else None
    return row


def insert_feedback(rows: list[dict]) -> list[str]:
    ids = []
    ts = now()
    with connect("agent") as conn:
        cur = conn.cursor()
        sql = _insert_sql(M.REVIEW_FEEDBACK, ["feedback_id", "invoice_id", "field_name", "agent_value",
                                               "corrected_value", "reviewer", "reviewed_at", "flag_cleared"])
        for r in rows:
            fid = str(uuid.uuid4())
            ids.append(fid)
            cur.execute(sql, _binds({"feedback_id": fid, "reviewed_at": ts, **r}))
    return ids


def get_feedback(invoice_id: int) -> list[dict]:
    F = M.REVIEW_FEEDBACK
    return _fetchall(f"SELECT {F.select()} FROM {F} WHERE {F.invoice_id} = :id ORDER BY {F.reviewed_at}",
                     {"id": invoice_id})


def ping() -> bool:
    _fetchone("SELECT 1 AS cnt FROM DUAL" if is_oracle() else "SELECT 1 AS cnt")
    return True


# ----------------------------------------------------------------------------
# admin-only (migrations / seeding). Never called by agents.
# ----------------------------------------------------------------------------
def admin_insert_many(table: M.Table, rows: list[dict], chunk: int = 2000) -> None:
    if not rows:
        return
    sql = _insert_sql(table, rows[0].keys())
    with connect("admin") as conn:
        cur = conn.cursor()
        for i in range(0, len(rows), chunk):
            cur.executemany(sql, [_binds(r) for r in rows[i:i + chunk]])


def admin_upsert_embeddings(rows: list[tuple[int, list[float]]], model_name: str) -> None:
    E = M.INVOICE_EMBEDDINGS
    sql = _insert_sql(E, ["invoice_id", "embedding", "model_name", "created_at"])
    ts = now()
    with connect("admin") as conn:
        cur = conn.cursor()
        if is_oracle():
            import oracledb
            cur.setinputsizes(embedding=oracledb.DB_TYPE_VECTOR)
        cur.executemany(sql, [_binds({"invoice_id": i, "embedding": vector_bind(v), "model_name": model_name,
                                      "created_at": ts}) for i, v in rows])
