"""Serve one report and the viewer page on localhost."""
from __future__ import annotations

import functools
import http.server
import webbrowser
from pathlib import Path

from organella.viewer.build import build


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, page: bytes, **kwargs):
        self._page = page
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        if self.path.split("?")[0] == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(self._page)))
            self.end_headers()
            self.wfile.write(self._page)
        else:
            super().do_GET()

    def log_message(self, *args) -> None:
        pass


def serve(report: Path, port: int, open_browser: bool) -> None:
    handler = functools.partial(_Handler, page=build().encode(), directory=str(report.parent))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        url = f"http://127.0.0.1:{port}/?data=/{report.name}"
        print(f"serving {url}  (Ctrl-C to stop)")
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
