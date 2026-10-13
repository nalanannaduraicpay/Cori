"""Static guardrails: agents never write SQL, no secrets in code, every
configured prompt exists and is versioned."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from cori.prompts.registry import load_prompt

ROOT = Path(__file__).resolve().parent.parent
SQL = re.compile(r"\b(SELECT\s+.+\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+TABLE)\b")


def test_sql_only_lives_in_the_db_package():
    offenders = []
    for path in (ROOT / "cori").rglob("*.py"):
        if "db" in path.relative_to(ROOT / "cori").parts:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if SQL.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}")
    assert not offenders, f"SQL outside cori/db: {offenders}"


def test_no_secrets_in_source():
    pattern = re.compile(r"(api[_-]?key|password)\s*=\s*['\"][^'\"]{6,}['\"]", re.IGNORECASE)
    hits = [str(p.relative_to(ROOT)) for p in (ROOT / "cori").rglob("*.py") if pattern.search(p.read_text("utf-8"))]
    assert not hits


def test_env_is_gitignored():
    assert ".env" in (ROOT / ".gitignore").read_text().split()


def test_configured_prompts_exist_and_are_versioned():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    ids = [e["prompt"] for e in cfg["extractors"].values()] + ["cori_summary.v1"]
    for pid in ids:
        assert re.fullmatch(r"[a-z_]+\.v\d+", pid)
        p = load_prompt(pid)
        assert p.version_tag.startswith(pid + "#")


def test_extractor_prompts_treat_document_as_untrusted():
    for pid in ("extractor_vision.v1", "extractor_text.v1", "extractor_text_alt.v1"):
        text = load_prompt(pid).text.lower()
        assert "untrusted" in text and ("ignore" in text or "never follow" in text)
