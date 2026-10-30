import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from viewer_tools import TOOLS, needs_tools

SKIPPED = 77

pytestmark = needs_tools


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def served(report_path):
    port = free_port()
    server = subprocess.Popen([sys.executable, "-c",
        f"from pathlib import Path; from organella.viewer.server import serve; serve(Path({str(report_path)!r}), {port}, False)"],
        stdout=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}/?data=/{report_path.name}"
    server.terminate()


def test_a_report_from_this_pipeline_draws_in_a_real_browser(served):
    run = subprocess.run(["node", "browser-check.mjs", served, "120"], cwd=TOOLS, capture_output=True, text=True, timeout=180)
    if run.returncode == SKIPPED:
        pytest.skip(run.stderr.strip())
    assert run.returncode == 0, run.stderr[-2000:]
    seen = json.loads(run.stdout)
    assert seen["loadError"] is None, seen["loadError"]
    assert seen["problems"] == []
    assert seen["drawn"] and seen["charts"] > 0, seen
