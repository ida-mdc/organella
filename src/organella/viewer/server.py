"""Serve one report and the viewer page on localhost."""
from __future__ import annotations

import functools
import http.server
import os
import re
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
        elif self.headers.get("Range") and self._send_range():
            return
        else:
            super().do_GET()

    def do_HEAD(self) -> None:
        # S3 and GitHub Pages answer a HEAD that carries a Range with 206 and the length of that range; DuckDB's reader
        # asks for `bytes=0-` and takes 206 to mean "this host does range reads".
        if not (self.headers.get("Range") and self._send_range(body=False)):
            super().do_HEAD()

    def end_headers(self) -> None:
        self.send_header("Accept-Ranges", "bytes")     # what lets the viewer read one fragment of a mesh file, not all of it
        super().end_headers()

    def _send_range(self, body: bool = True) -> bool:
        """Answer a single ``bytes=a-b`` / ``bytes=a-`` / ``bytes=-n`` request for a file with 206 or 416; False if this is not one."""
        path = self.translate_path(self.path)
        found = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers["Range"].strip())
        if not found or not os.path.isfile(path) or found.group(1) == found.group(2) == "":
            return False            # several ranges, another unit or a directory: the whole file is a correct answer
        size = os.path.getsize(path)
        first, last = found.groups()
        start, stop = (max(0, size - int(last)), size - 1) if first == "" else (int(first), min(int(last or size - 1), size - 1))
        if start >= size or start > stop:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{stop}/{size}")
        self.send_header("Content-Length", str(stop - start + 1))
        self.end_headers()
        if not body:
            return True
        with open(path, "rb") as handle:
            handle.seek(start)
            remaining = stop - start + 1
            while remaining > 0:
                chunk = handle.read(min(1 << 20, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        return True

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
