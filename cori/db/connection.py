"""Database connections.

* ``oracle`` (default/target): python-oracledb thin mode, one pool per role.
  - ``admin`` role = schema owner (migrations, seeding)
  - ``agent`` role = least-privilege user used by Cori at runtime; its session
    sets CURRENT_SCHEMA to the owner so unqualified table names resolve.
* ``sqlite`` (dev/test fallback only): same schema and named-bind SQL;
  ``VECTOR_DISTANCE`` is registered as a Python function. Roles are not
  enforced in SQLite.
"""
from __future__ import annotations

import array
import json
import math
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from cori.config import ROOT, get_settings

_pools: dict[str, object] = {}
_lock = threading.Lock()


def backend() -> str:
    return get_settings().db_backend


def is_oracle() -> bool:
    return backend() == "oracle"


# ----------------------------------------------------------------------------
# Oracle
# ----------------------------------------------------------------------------
def _oracle_pool(role: str):
    import oracledb

    oracledb.defaults.fetch_decimals = True  # NUMBER -> Decimal, never float
    oracledb.defaults.fetch_lobs = False      # CLOB -> str
    with _lock:
        if role in _pools:
            return _pools[role]
        if role == "admin":
            user, pwd = os.environ["ORACLE_ADMIN_USER"], os.environ["ORACLE_ADMIN_PASSWORD"]
        else:
            user, pwd = os.environ["ORACLE_AGENT_USER"], os.environ["ORACLE_AGENT_PASSWORD"]
        owner = os.environ.get("ORACLE_ADMIN_USER", "CORI_OWNER").upper()

        def init_session(conn, _requested_tag):
            if role != "admin":
                with conn.cursor() as cur:
                    cur.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {owner}")

        pool = oracledb.create_pool(user=user, password=pwd, dsn=os.environ["ORACLE_DSN"],
                                    min=1, max=max(4, get_settings().max_concurrent_invoices * 3),
                                    increment=1, session_callback=init_session)
        _pools[role] = pool
        return pool


def oracle_system_connection():
    """SYSTEM connection, used once by migrate.py to create the two users."""
    import oracledb
    return oracledb.connect(user="SYSTEM", password=os.environ["ORACLE_PWD"], dsn=os.environ["ORACLE_DSN"])


# ----------------------------------------------------------------------------
# SQLite
# ----------------------------------------------------------------------------
def sqlite_path() -> Path:
    p = Path(os.getenv("SQLITE_PATH", "data/cori.db"))
    return p if p.is_absolute() else ROOT / p


def _vector_distance(a: str, b: str, metric: str = "COSINE") -> float | None:
    if a is None or b is None:
        return None
    va, vb = json.loads(a), json.loads(b)
    metric = (metric or "COSINE").upper()
    if metric == "EUCLIDEAN":
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(va, vb)))
    if metric == "DOT":
        return -sum(x * y for x, y in zip(va, vb))
    dot = sum(x * y for x, y in zip(va, vb))
    na = math.sqrt(sum(x * x for x in va))
    nb = math.sqrt(sum(y * y for y in vb))
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - dot / (na * nb)


def _sqlite_connect() -> sqlite3.Connection:
    path = sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.create_function("VECTOR_DISTANCE", 3, _vector_distance, deterministic=True)
    return conn


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------
@contextmanager
def connect(role: str = "agent") -> Iterator[object]:
    """Yield a DB-API connection; commits on success, rolls back on error."""
    if is_oracle():
        pool = _oracle_pool(role)
        conn = pool.acquire()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            pool.release(conn)
    else:
        conn = _sqlite_connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def close_pools() -> None:
    with _lock:
        for pool in _pools.values():
            try:
                pool.close(force=True)
            except Exception:
                pass
        _pools.clear()


# --- dialect helpers ---------------------------------------------------------
def limit(n: int) -> str:
    n = int(n)
    return f" FETCH FIRST {n} ROWS ONLY" if is_oracle() else f" LIMIT {n}"


def vector_bind(vec: list[float]):
    if is_oracle():
        return array.array("f", vec)
    return json.dumps([round(float(x), 6) for x in vec])


def vector_distance(column: str, bind_name: str) -> str:
    metric = "COSINE" if is_oracle() else "'COSINE'"
    return f"VECTOR_DISTANCE({column}, :{bind_name}, {metric})"
