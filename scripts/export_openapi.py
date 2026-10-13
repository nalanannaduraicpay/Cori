"""Write the OpenAPI spec to docs/openapi.json (for the C# team / NSwag)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cori.api.main import app  # noqa: E402

if __name__ == "__main__":
    out = ROOT / "docs" / "openapi.json"
    out.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
    print(f"wrote {out}")
