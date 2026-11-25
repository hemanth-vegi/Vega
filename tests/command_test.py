"""Static command diagnostics that never instantiate Vega or execute actions.

Run from the repository root with: python tests/command_test.py
"""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (ROOT / "vega_core.py", ROOT / "vega_control.py")

for source in SOURCES:
    try:
        ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    except (OSError, SyntaxError) as exc:
        print(f"[FAIL] {source.name}: {type(exc).__name__}")
        raise SystemExit(1)
    print(f"[OK] {source.name} parses successfully")

print("[SAFE] Static-only diagnostic; no Vega instance, desktop command, or user data was accessed.")
