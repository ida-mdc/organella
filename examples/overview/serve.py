#!/usr/bin/env -S uv run
"""Serve the overview and every report on it, so each card's link opens that report with its geometry.

    uv run examples/overview/serve.py DATA [--port 8060]

Run it from the organella project (`uv run` there), since it builds the page `organella view` serves.
`/` is overview.html; `/organella_report.html` is the viewer; `/r/<id>/<report>.parquet` is a report and
`/r/<id>/g/` its geometry folder, `<report stem>_meshes` beside it, for each entry of reports.json.
"""
import argparse
import http.server
import json
import threading
import urllib.parse
import webbrowser
from functools import partial
from pathlib import Path

from organella.viewer.build import build

HERE = Path(__file__).resolve().parent
PAGE = "/organella_report.html"


class Handler(http.server.SimpleHTTPRequestHandler):
    """Static files from this folder, the viewer page, and `/r/<id>/` pointing at each report's own folder."""

    mounts: dict[str, tuple[Path, Path]] = {}   # per id: the report and its geometry folder

    def __init__(self, *args, page: bytes, **kwargs):
        self._page = page
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        if self.path.split("?")[0] == PAGE:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(self._page)))
            self.end_headers()
            self.wfile.write(self._page)
        else:
            super().do_GET()

    def translate_path(self, path: str) -> str:
        clean = urllib.parse.unquote(path.split("?", 1)[0].split("#", 1)[0])
        if clean in ("/", "/index.html"):
            return str(HERE / "overview.html")
        parts = [p for p in clean.split("/") if p not in ("", ".", "..")]
        if len(parts) >= 3 and parts[0] == "r" and parts[1] in self.mounts:
            report, geometry = self.mounts[parts[1]]
            if parts[2] == "g":
                return str(geometry.joinpath(*parts[3:]))
            if parts[2] == report.name:
                return str(report)
        return super().translate_path(path)

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("data", type=Path)
    ap.add_argument("--port", type=int, default=8060)
    args = ap.parse_args()
    Handler.mounts = {}
    for entry in json.loads((HERE / "reports.json").read_text()):
        report = (args.data / entry["report"]).resolve()
        if report.is_file():
            Handler.mounts[entry["id"]] = (report, report.parent / f"{report.stem}_meshes")
    handler = partial(Handler, directory=str(HERE), page=build().encode())
    url = f"http://127.0.0.1:{args.port}/"
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"{len(Handler.mounts)} reports at {url}  (Ctrl-C to stop)")
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
