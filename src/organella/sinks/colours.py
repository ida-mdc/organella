from __future__ import annotations

import json
import re
from pathlib import Path

import click

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def colours_from_file(path: Path) -> dict[str, str]:
    """Structure colours from a JSON file ``{"mito": "#d62728"}``. Names the batch does not have are ignored, so one file
    can cover a project; structures the file does not name keep the page's own palette."""
    try:
        chosen = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise click.ClickException(f"{path} is not JSON: {error}") from error
    if not isinstance(chosen, dict):
        raise click.ClickException(f'{path} should hold an object like {{"mito": "#d62728"}}')
    wrong = {name: value for name, value in chosen.items() if not (isinstance(value, str) and _HEX.match(value))}
    if wrong:
        name, value = next(iter(wrong.items()))
        raise click.ClickException(f"{path}: {name!r} is {value!r}, which is not a colour like #d62728")
    return dict(chosen)
