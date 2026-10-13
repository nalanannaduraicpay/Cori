-- Create the two Cori database users. Run ONCE as a DBA (SYSDBA or SYSTEM),
-- before scripts/migrate.py. Not executed by migrate.py's statement splitter.
--
--   sqlplus "/ as sysdba" @000_create_users.sql FREEPDB1 CORI_OWNER <owner_pwd> CORI_AGENT <agent_pwd>
--
-- (Docker path: scripts/migrate.py does the same thing as SYSTEM when
--  ORACLE_PWD is set in .env, so this script is only needed when the SYSTEM
--  password is not available, e.g. a native Windows install using OS auth.)
--
-- CORI_OWNER = admin user: owns the schema, runs migrations and seeding.
-- CORI_AGENT = runtime user: CREATE SESSION only; table-level grants are
--              applied by 002_agent_grants.sql (read-only on historical
--              tables, write on AGENT_* / embeddings / decisions).

WHENEVER SQLERROR EXIT SQL.SQLCODE
SET VERIFY OFF

ALTER SESSION SET CONTAINER = &1;

CREATE USER IF NOT EXISTS &2 IDENTIFIED BY "&3" DEFAULT TABLESPACE USERS QUOTA UNLIMITED ON USERS;
ALTER USER &2 IDENTIFIED BY "&3";
GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE, CREATE VIEW TO &2;

CREATE USER IF NOT EXISTS &4 IDENTIFIED BY "&5";
ALTER USER &4 IDENTIFIED BY "&5";
GRANT CREATE SESSION TO &4;

PROMPT Cori users ready in &1: &2 (owner), &4 (agent)
EXIT
