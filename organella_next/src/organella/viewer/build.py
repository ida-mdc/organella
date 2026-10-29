"""Assemble the standalone report page from this folder: ``python -m organella.viewer.build [OUTPUT]``.

``index.html`` holds the markup. ``/*@styles/x.css*/`` and ``/*@src*/`` are replaced by the files
of ``styles/`` and by the files of ``src/`` in the order ``load-order.txt`` lists them (they share globals, so order matters). ``@name.ext`` inside a ``data:`` URI is replaced by
the base64 of ``assets/name.ext``, so the page stays one file with nothing beside it.
"""
import base64
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
MIME = {"png": "image/png", "woff2": "font/woff2"}


def inline_assets(text: str) -> str:
    def encode(match: re.Match) -> str:
        name = match["name"]
        data = base64.b64encode((HERE / "assets" / name).read_bytes()).decode()
        return f"data:{MIME[name.rsplit('.', 1)[1]]};base64,{data}"

    return re.sub(r"data:[a-z0-9/+-]+;base64,@(?P<name>[\w.-]+)", encode, text)


def sections() -> list[str]:
    listed = (HERE / "load-order.txt").read_text().split()
    on_disk = {p.relative_to(HERE / "src").with_suffix("").as_posix() for p in (HERE / "src").rglob("*.js")}
    unlisted = on_disk - set(listed)
    if unlisted:
        raise ValueError(f"not in load-order.txt: {sorted(unlisted)}")
    return listed


def build() -> str:
    page = (HERE / "index.html").read_text()
    for marker in re.findall(r"/\*@(styles/[\w.-]+)\*/", page):
        page = page.replace(f"/*@{marker}*/", (HERE / marker).read_text().rstrip("\n"))
    scripts = "".join((HERE / "src" / f"{name}.js").read_text() for name in sections())
    page = page.replace("/*@src*/", scripts.rstrip("\n"))
    return inline_assets(page)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "dist" / "organella_report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build())
