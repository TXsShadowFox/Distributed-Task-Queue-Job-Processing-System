#!/usr/bin/env python3
"""Export the API's OpenAPI document to packages/shared-schemas/openapi.json.

The dashboard generates its TypeScript types from this file (``npm run gen:api`` in apps/web), so the frontend and the
API share one contract. CI re-exports it and fails if the committed copy is stale.

    python scripts/export_openapi.py          # write
    python scripts/export_openapi.py --check  # exit 1 if the committed file differs
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packages" / "shared-schemas" / "openapi.json"


def render() -> str:
    # Settings require a signing secret even though nothing is signed here; use a throwaway value.
    os.environ.setdefault("FLOWFORGE_JWT_SECRET", secrets.token_urlsafe(48))
    from flowforge_api.app import create_app

    spec = create_app().openapi()
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main() -> None:
    content = render()
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != content:
            print(f"{OUT.relative_to(ROOT)} is stale; run `python scripts/export_openapi.py`")
            sys.exit(1)
        print("openapi.json is up to date")
        return
    OUT.write_bytes(content.encode("utf-8"))
    print(f"wrote {OUT.relative_to(ROOT)} ({len(content):,} bytes)")


if __name__ == "__main__":
    main()
