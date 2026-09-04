"""The report page, and the one-command way to open a report in it.

A report is read by a single standalone HTML file: open it, drop a ``report.parquet`` on
it, and every chart is drawn in the browser from that file. Nothing is uploaded and no
server is needed, which is the whole point - a report can be mailed to a collaborator with
a link to the page and they can read it.

Geometry is the one thing that does not fit that model. It lives beside the report, one
``geometry.parquet`` per object, and the object row records where: an absolute path, which
is what DuckDB needs when it runs natively but means nothing to a browser. The page
therefore takes geometry two ways - a folder handed over by hand, or a base URL it reads
over HTTP - and :func:`serve` is the second one automated: it serves the report, its
geometry and the page from one localhost origin and opens the page pointed at both.
"""

from __future__ import annotations

import http.server
import socket
import threading
import urllib.parse
import webbrowser
from functools import partial
from pathlib import Path
from typing import Optional

PAGE_FILENAME = "anatomy_report.html"

# Where the served geometry root is mounted. The geometry can sit outside the report's own
# folder (``anatomy mesh -o geometry/``), so it gets a mount of its own rather than being
# assumed to be a sibling.
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
