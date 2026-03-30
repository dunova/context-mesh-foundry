from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SRC_PKG = ROOT / "src" / "contextgo"
SCRIPTS = ROOT / "scripts"

for path in (str(SRC), str(SRC_PKG), str(SCRIPTS)):
    if path not in sys.path:
        sys.path.insert(0, path)
