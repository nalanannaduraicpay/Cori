#!/usr/bin/env bash
# Runs once when the codespace / dev container is created.
set -euo pipefail
cd "$(dirname "$0")/.."

python -m pip install --user --upgrade pip
python -m pip install --user -r requirements.txt

# .env is gitignored: generate one for the in-container Oracle if it is missing.
# ORACLE_PWD must match docker-compose.devcontainer.yml; scripts/migrate.py uses
# it (as SYSTEM) to create CORI_OWNER and CORI_AGENT with the random passwords.
if [ ! -f .env ]; then
  python - <<'PY'
import os
import re
import secrets
from pathlib import Path

values = {
    "DB_BACKEND": "oracle",
    "ORACLE_DSN": "oracle:1521/FREEPDB1",
    "ORACLE_PWD": os.environ.get("ORACLE_PWD") or "CoriDevOnly_2026",
    "ORACLE_ADMIN_PASSWORD": "Cori_" + secrets.token_hex(10) + "_9",
    "ORACLE_AGENT_PASSWORD": "Cori_" + secrets.token_hex(10) + "_9",
    "LLM_PROVIDER": "mock",
}
text = Path(".env.example").read_text(encoding="utf-8")
for key, value in values.items():
    text = re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.MULTILINE)
Path(".env").write_text(text, encoding="utf-8")
print("wrote .env (Oracle in container, mock LLM). Add API keys there or as Codespaces secrets.")
PY
fi

echo
echo "Next:  python scripts/wait_for_db.py && python scripts/run_demo.py"
echo "UI:    python scripts/setup_demo.py && uvicorn cori.api.main:app --host 0.0.0.0 --port 8000"
