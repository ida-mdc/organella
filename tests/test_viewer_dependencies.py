import subprocess
from pathlib import Path

import pytest
from viewer_tools import TOOLS, needs_tools


pytestmark = needs_tools


def test_every_name_a_viewer_file_reads_is_declared_once_and_ready_when_read():
    run = subprocess.run(["node", "dependencies.mjs", "--check"], cwd=TOOLS, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout[-3000:]


def test_the_built_page_runs_its_script_to_the_end_with_no_dom(tmp_path):
    from organella.viewer.build import build

    page = tmp_path / "page.html"
    page.write_text(build())
    run = subprocess.run(["node", "load-check.mjs", str(page)], cwd=TOOLS, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr[-2000:]
    assert {"boot", "renderScene", "adapt"} <= set(run.stdout.split())
