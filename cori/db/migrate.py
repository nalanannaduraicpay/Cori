"""Apply migrations. Oracle: creates the owner + agent users (as SYSTEM), the
schema (as owner) and least-privilege grants. SQLite: recreates the file."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from cori.db import mapping as M
from cori.db.connection import close_pools, connect, is_oracle, oracle_system_connection, sqlite_path

log = logging.getLogger(__name__)
MIGRATIONS = Path(__file__).parent / "migrations"
_SAFE = re.compile(r"^[A-Za-z0-9_#$!%^*+=.-]+$")


def _split_oracle(sql: str) -> list[str]:
    stmts, buf = [], []
    for line in sql.splitlines():
        if line.strip() == "/":
            stmt = "\n".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
        elif not line.strip().startswith("--"):
            buf.append(line)
    tail = "\n".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


def _ensure_oracle_users() -> None:
    """Create the owner + agent users as SYSTEM. Skipped when ORACLE_PWD is
    blank: the users were then provisioned by a DBA with 000_create_users.sql."""
    if not os.environ.get("ORACLE_PWD", "").strip():
        log.info("ORACLE_PWD not set; assuming users were created with 000_create_users.sql")
        return
    owner, owner_pwd = os.environ["ORACLE_ADMIN_USER"].upper(), os.environ["ORACLE_ADMIN_PASSWORD"]
    agent, agent_pwd = os.environ["ORACLE_AGENT_USER"].upper(), os.environ["ORACLE_AGENT_PASSWORD"]
    for value in (owner, owner_pwd, agent, agent_pwd):
        if not _SAFE.match(value):  # DDL cannot use binds; refuse anything unusual
            raise ValueError("Oracle user names/passwords may only contain letters, digits and _#$!%^*+=.-")
    with oracle_system_connection() as conn:
        cur = conn.cursor()
        for user, pwd, privs in (
            (owner, owner_pwd, "CREATE SESSION, CREATE TABLE, CREATE SEQUENCE, CREATE VIEW"),
            (agent, agent_pwd, "CREATE SESSION"),
        ):
            cur.execute("SELECT COUNT(*) FROM dba_users WHERE username = :u", {"u": user})
            if cur.fetchone()[0] == 0:
                log.info("creating Oracle user %s", user)
                cur.execute(f'CREATE USER {user} IDENTIFIED BY "{pwd}"')
            cur.execute(f"GRANT {privs} TO {user}")
        cur.execute(f"ALTER USER {owner} QUOTA UNLIMITED ON USERS")


def migrate(reset: bool = True) -> None:
    close_pools()
    if is_oracle():
        _ensure_oracle_users()
        with connect("admin") as conn:
            cur = conn.cursor()
            if reset:
                for table in M.ALL_TABLES:
                    try:
                        cur.execute(f"DROP TABLE {table} CASCADE CONSTRAINTS PURGE")
                    except Exception as exc:  # ORA-00942: table does not exist
                        if "ORA-00942" not in str(exc):
                            raise
            for stmt in _split_oracle((MIGRATIONS / "oracle" / "001_schema.sql").read_text(encoding="utf-8")):
                cur.execute(stmt)
            agent = os.environ["ORACLE_AGENT_USER"].upper()
            grants = (MIGRATIONS / "oracle" / "002_agent_grants.sql").read_text(encoding="utf-8")
            for stmt in _split_oracle(grants.replace("{AGENT}", agent)):
                cur.execute(stmt)
    else:
        path = sqlite_path()
        if reset:
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(path) + suffix)
                if p.exists():
                    p.unlink()
        with connect("admin") as conn:
            conn.executescript((MIGRATIONS / "sqlite" / "001_schema.sql").read_text(encoding="utf-8"))
    log.info("migrations applied (%s)", "oracle" if is_oracle() else "sqlite")
