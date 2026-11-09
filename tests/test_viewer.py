"""The report page: built as one file, its script checked by node, and drawn in a real browser. The node tools are
missing locally, the tests skip; under CI that is a failure."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from organella.viewer.build import build

TOOLS = Path(__file__).parent.parent / "tools" / "viewer"
SKIPPED = 77   # the exit code browser-check.mjs uses when there is no Chrome to drive

if not (READY := shutil.which("node") is not None and (TOOLS / "node_modules").exists()) and os.environ.get("CI"):
    raise RuntimeError("the viewer tests need node and `npm ci` in tools/viewer")
needs_tools = pytest.mark.skipif(not READY, reason="needs node and `npm ci` in tools/viewer")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(params=["2d", "3d"])
def served(synthetic_reports, request):
    report, port = synthetic_reports[request.param], free_port()
    server = subprocess.Popen([sys.executable, "-c",
        f"from pathlib import Path; from organella.viewer.server import serve; serve(Path({str(report)!r}), {port}, False)"],
        stdout=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield request.param, f"http://127.0.0.1:{port}/?data=/{report.name}"
    server.terminate()


def test_page_is_one_file_with_nothing_left_to_fill_in():
    page = build()
    assert "/*@" not in page and "base64,@" not in page
    assert page.count("<script>") == 1 and "data:font/woff2;base64," in page


@needs_tools
def test_every_name_a_viewer_file_reads_is_declared_once_and_ready_when_read():
    run = subprocess.run(["node", "dependencies.mjs", "--check"], cwd=TOOLS, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout[-3000:]


@needs_tools
def test_the_fragment_cut_follows_the_camera_covers_the_surface_once_and_never_leaves_a_hole():
    run = subprocess.run(["node", "lod-check.mjs"], cwd=TOOLS, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr[-3000:]


@needs_tools
def test_the_built_page_runs_its_script_to_the_end_with_no_dom(tmp_path):
    page = tmp_path / "page.html"
    page.write_text(build())
    run = subprocess.run(["node", "load-check.mjs", str(page)], cwd=TOOLS, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr[-2000:]
    assert {"boot", "renderScene", "adapt"} <= set(run.stdout.split())


@needs_tools
def test_a_report_from_this_pipeline_draws_in_a_real_browser_and_knows_whether_it_is_a_plane(served):
    dimension, url = served
    run = subprocess.run(["node", "browser-check.mjs", url, "120"], cwd=TOOLS, capture_output=True, text=True, timeout=180)
    if run.returncode == SKIPPED:
        pytest.skip(run.stderr.strip())
    assert run.returncode == 0, run.stderr[-2000:]
    seen = json.loads(run.stdout)
    assert seen["loadError"] is None, seen["loadError"]
    assert seen["problems"] == [] and seen["drawn"] and seen["charts"] > 0, seen
    assert (seen["planar"], seen["pin"]) == (dimension == "2d", dimension.upper()), seen   # a plane says 2D on its buttons


SERVE_AND_LOG = """
import functools, http.server, sys
from pathlib import Path
from organella.viewer import server
from organella.viewer.build import build
report, port, log = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
class Logged(server._Handler):
    def log_request(self, code="-", size="-"):
        with open(log, "a") as out:
            out.write(f"{self.command} {self.path} {self.headers.get('Range')} {code}\\n")
handler = functools.partial(Logged, page=build().encode(), directory=str(report.parent))
http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
"""


@needs_tools
def test_big_meshes_are_refined_from_byte_ranges_as_the_camera_closes_in(multires_report, tmp_path):
    port, log = free_port(), tmp_path / "requests.log"
    server = subprocess.Popen([sys.executable, "-c", SERVE_AND_LOG, str(multires_report), str(port), str(log)], stdout=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        run = subprocess.run(["node", "multires-check.mjs", f"http://127.0.0.1:{port}/?data=/{multires_report.name}", "150"],
                             cwd=TOOLS, capture_output=True, text=True, timeout=240)
    finally:
        server.terminate()
    if run.returncode == SKIPPED:
        pytest.skip(run.stderr.strip())
    assert run.returncode == 0, run.stderr[-2000:]
    seen = json.loads(run.stdout)
    assert seen["problems"] == [] and seen["records"] > 0, seen
    assert seen["finest"] > 0 and seen["failed"] == 0, seen                  # an instance reached its finest level
    assert seen["statuses"] == [206] and seen["rangeRequests"] > 0, seen    # by byte range, not whole files
    assert seen["whole"]["read"] == seen["whole"]["wanted"] > 0, seen       # the pop-up's mesh is the finest level whole
    assert seen["pinned"]["explain"] and seen["pinned"]["records"] > 0 and seen["pinned"]["refined"] > 0, seen   # and with a chart pinned
    # DuckDB reads the geometry file by byte range too: a whole-file GET is the page downloading everything before it draws.
    geometry = [line.split() for line in log.read_text().splitlines() if "geometry.parquet" in line and line.startswith("GET")]
    assert geometry and all(line[-2] != "None" and line[-1] == "206" for line in geometry), geometry
