import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
EXISTING = Path("/mnt/data/current_platform/extracted/app")
sys.path.insert(0, str(BASE))
sys.path.insert(1, "/mnt/data/current_platform/extracted")

import app  # noqa: E402

if str(EXISTING) not in app.__path__:
    app.__path__.append(str(EXISTING))
