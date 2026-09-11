"""The report page, and the one-command way to open a report in it.

A report is read by a single standalone HTML file: open it, drop a ``report.parquet`` on it,
and every chart is drawn in the browser. Nothing is uploaded and no server is needed, which
is the point - a report can be mailed to a collaborator with a link to the page.

Geometry does not fit that model. It lives beside the report, one ``geometry.parquet`` per
object, and the object row records an absolute path, which is what DuckDB needs natively and
nothing to a browser. So the page takes geometry two ways, a folder handed over by hand or a
base URL over HTTP, and :func:`serve` automates the second: report, geometry and page from
one localhost origin, with the page pointed at both.
"""

from __future__ import annotations

import http.server
import os
import socket
import threading
import urllib.parse
import webbrowser
from functools import partial
from http import HTTPStatus
from pathlib import Path
from typing import Optional, Tuple

PAGE_FILENAME = "organella_report.html"

# Where the served geometry root is mounted. ``organella mesh -o geometry/`` can put it
# outside the report's own folder, so it gets a mount rather than being assumed a sibling.
GEOMETRY_MOUNT = "__geometry"


def report_page() -> Path:
    """The standalone report page that ships with this package."""
    page = Path(__file__).parent / "report" / PAGE_FILENAME
    if not page.is_file():                        # pragma: no cover - a broken install
        raise FileNotFoundError(f"the report page is missing from this install: {page}")
    return page


def geometry_root(report: Path) -> Optional[Path]:
    """The folder holding this report's geometry, read off the report rather than guessed.

    ``mesh_geometry_file`` is ``<root>/<object_id>/geometry.parquet``, so the root is two
    levels up from any of them. Taken from the report because ``--mesh-dir`` can put it
    anywhere; a report with no geometry, or whose geometry has since moved, gives None.
    """
    try:
        import polars as pl

        paths = (pl.scan_parquet(report)
                 .select("mesh_geometry_file")
                 .drop_nulls()
                 .unique()
                 .collect()
                 .to_series()
                 .to_list())
    except Exception:      # noqa: BLE001 - no such column, or an unreadable report
        return None
    for path in paths:
        root = Path(str(path)).parent.parent
        if root.is_dir():
            return root
    return None


def _parse_range(header: str, size: int) -> Optional[Tuple[int, int]]:
    """``bytes=0-99`` as (start, length), or None if it asks for nothing readable.

    One range only. A reader that asks for several gets the whole file instead, which is
    correct if wasteful; multipart/byteranges is not worth carrying for a local server.
    """
    units, _, spec = header.partition("=")
    if units.strip().lower() != "bytes" or "," in spec:
        return None
    first, _, last = spec.strip().partition("-")
    try:
        if not first:                       # bytes=-500: the final 500 bytes
            length = min(int(last), size)
            return (size - length, length) if length > 0 else None
        start = int(first)
        end = int(last) if last else size - 1
    except ValueError:
        return None
    end = min(end, size - 1)
    if start > end or start >= size:
        return None
    return start, end - start + 1


class _Handler(http.server.SimpleHTTPRequestHandler):
    """The report's folder at ``/``, the page and the geometry mounted beside it."""

    page: Path
    geometry: Optional[Path]

    def translate_path(self, path: str) -> str:
        clean = urllib.parse.unquote(path.split("?", 1)[0].split("#", 1)[0])
        if clean.lstrip("/") == PAGE_FILENAME:
            return str(self.page)
        prefix = f"/{GEOMETRY_MOUNT}/"
        if clean.startswith(prefix) and self.geometry is not None:
            # Drop every empty, "." and ".." segment before joining, so a request cannot
            # walk out of the geometry root.
            parts = [part for part in clean[len(prefix):].split("/")
                     if part not in ("", ".", "..")]
            return str(Path(self.geometry).joinpath(*parts))
        return super().translate_path(path)

    #: The byte range this request asked for, as (start, length), or None for the whole file.
    _range: Optional[Tuple[int, int]] = None

    def send_head(self):
        """The file to send, as a range when one was asked for.

        A report and its geometry are parquet, and a parquet reader that can ask for bytes
        reads only the footer and the column chunks its query touches: opening the 3D
        section counts rows, which is a few kB of a nine-megabyte file. Without this the
        whole file crosses for every query, so the laziness the queries are written for
        never arrives.
        """
        self._range = None
        asked = self.headers.get("Range")
        if not asked:
            return super().send_head()
        path = self.translate_path(self.path)
        try:
            size = os.path.getsize(path)
            handle = open(path, "rb")
        except OSError:
            return super().send_head()      # let the base class 404 or list it
        span = _parse_range(asked, size)
        if span is None:
            handle.close()
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        start, length = span
        self._range = span
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Range", f"bytes {start}-{start + length - 1}/{size}")
        self.end_headers()
        return handle

    def copyfile(self, source, outputfile) -> None:
        """Only the bytes that were asked for."""
        if self._range is None:
            return super().copyfile(source, outputfile)
        start, remaining = self._range
        source.seek(start)
        while remaining > 0:
            chunk = source.read(min(64 * 1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)

    def end_headers(self) -> None:
        """Never let a browser keep any of this.

        Every report is served from the same port at the same paths, so a geometry file is
        ``/__geometry/<object_id>/geometry.parquet`` whichever run wrote it, and
        ``Last-Modified`` alone invites heuristic caching. View a batch, mesh it again, view
        it again, and the browser can answer for some objects out of the copy it kept:
        DuckDB then gets a glob written by two versions of the writer and refuses it
        ("schema mismatch in glob"), which reads as broken geometry rather than a stale
        cache. Everything here is local and re-read in milliseconds.
        """
        self.send_header("Cache-Control", "no-store")
        # Said on every response, since a reader decides whether to ask for ranges before
        # it has asked for anything.
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def log_message(self, *args) -> None:    # noqa: D102 - one line per request is noise
        pass


def open_url(report: Path, port: int, host: str = "127.0.0.1") -> str:
    """The URL that opens this report in the page, with its geometry attached."""
    url = f"http://{host}:{port}/{PAGE_FILENAME}?data={report.name}"
    if geometry_root(report) is not None:
        url += f"&geometry={GEOMETRY_MOUNT}"
    return url


def serve(report: Path, port: int = 8052, open_browser: bool = True) -> str:
    """Serve the page, the report and its geometry from one origin; return the URL.

    One origin, because the page reads the geometry over HTTP and a browser will not fetch
    it from another. Blocks until interrupted.
    """
    report = report.resolve()
    handler = partial(_Handler, directory=str(report.parent))
    _Handler.page = report_page()
    _Handler.geometry = geometry_root(report)
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as exc:
        raise SystemExit(f"Could not listen on port {port}: {exc}\n"
                         "Another process is using it; pass --port.") from exc
    url = open_url(report, server.server_address[1])
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return url


def free_port() -> int:
    """A port the OS says is free, for a caller that does not care which."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
