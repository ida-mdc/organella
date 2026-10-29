"""The node tools the viewer tests run. Missing locally they skip; missing under CI they are a failure."""
import os
import shutil
from pathlib import Path

import pytest

TOOLS = Path(__file__).parent.parent / "tools" / "viewer"
READY = shutil.which("node") is not None and (TOOLS / "node_modules").exists()

if not READY and os.environ.get("CI"):
    raise RuntimeError("the viewer tests need node and `npm ci` in tools/viewer")

needs_tools = pytest.mark.skipif(not READY, reason="needs node and `npm install` in tools/viewer")
