"""Versioned prompts. Files are named ``<prompt_id>.v<N>.md`` and referenced by
their full version ID (e.g. ``extractor_text.v1``) from config.yaml, so every
AGENT_RUNS row records exactly which prompt text produced it."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Prompt:
    version_id: str  # e.g. extractor_text.v1
    text: str
    sha256: str

    @property
    def version_tag(self) -> str:
        """Version ID plus short content hash, logged on every run."""
        return f"{self.version_id}#{self.sha256[:8]}"


@lru_cache
def load_prompt(version_id: str) -> Prompt:
    path = PROMPT_DIR / f"{version_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt {version_id!r} not found in {PROMPT_DIR}")
    text = path.read_text(encoding="utf-8").strip()
    return Prompt(version_id, text, hashlib.sha256(text.encode()).hexdigest())
