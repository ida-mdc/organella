#!/usr/bin/env -S uv run
"""Serve the overview and every report on it, so each card's link opens that report with its geometry.

    uv run examples/overview/serve.py DATA [--port 8060]

Run it from the organella project (`uv run` there), since it reuses the server `organella view` runs.
`/` is overview.html; `/organella_report.html` is the viewer; `/r/<id>/<report>.parquet` is a report and
`/r/<id>/g/` its geometry, for each entry of reports.json.
"""
import argparse
import json
import threading
import urllib.parse
import webbrowser
from functools import partial
from pathlib import Path

from organella.report_page import PAGE_FILENAME, _Handler, _Server, geometry_root, report_page

HERE = Path(__file__).resolve().parent


class Handler(_Handler):
    """The base handler, with `/r/<id>/` pointing at each report's folder and geometry."""

    mounts: dict = {}

    def translate_path(self, path: str) -> str:
        clean = urllib.parse.unquote(path.split("?", 1)[0].split("#", 1)[0])
        if clean in ("/", "/index.html"):
            return str(HERE / "overview.html")
        parts = [p for p in clean.split("/") if p not in ("", ".", "..")]
        if len(parts) >= 3 and parts[0] == "r" and parts[1] in self.mounts:
            report, geometry = self.mounts[parts[1]]
            if parts[2] == "g" and geometry is not None:
                return str(geometry.joinpath(*parts[3:]))
            if parts[2] == report.name:
                return str(report)
        return super().translate_path(path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("data", type=Path)
    ap.add_argument("--port", type=int, default=8060)
    args = ap.parse_args()
    Handler.mounts = {}
    for entry in json.loads((HERE / "reports.json").read_text()):
        report = (args.data / entry["report"]).resolve()
        if report.is_file():
            Handler.mounts[entry["id"]] = (report, geometry_root(report))
    handler = partial(Handler, directory=str(HERE), page=report_page(), geometry=None)
    server = _Server(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"{len(Handler.mounts)} reports at {url}  (Ctrl-C to stop)")
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
