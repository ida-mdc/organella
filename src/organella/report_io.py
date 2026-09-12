"""Writing the report, and reading one back.

A report is one parquet file: the rows, a description on every column, and a footer saying
how the run was made. That is the whole format, and this module is all of it.

The descriptions are why the page can explain a metric under the chart of it:
`organella_column_descriptions` holds the whole column -> description map as JSON. The
per-field metadata carries it too, which makes the file self-describing to `pyarrow`, but a
browser cannot reach that - DuckDB drops field metadata - so the page reads the footer copy.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from organella.measure.loading import LOADER_NAME
from organella.model.report import Report

logger = logging.getLogger(__name__)


class EmptyReport(RuntimeError):
    """Raised rather than writing a report with no rows: see :func:`write`."""


PRIVACY_SUMMARY = [
    "- object folder paths and the names of the volumes read",
    "- voxel size, extent and per-structure measurements",
    "- no pixel data: geometry is written beside the report, never into it",
]

#: Footer key holding the whole column -> description map.
DESCRIPTIONS_KEY = "organella_column_descriptions"

#: What one measured thing is called, if the run said. Presentation only: columns stay
#: `object_*`, since renaming them per run would make two reports unjoinable.
NOUN_KEY = "organella_object_noun"

#: What the provenance keys are called.
FOOTER_PREFIX = "organella_"


def footer_metadata(
    *,
    project_name: str,
    flavor: str,
    root: Optional[Path],
    paths: Sequence[str],
    processing_stats: Dict[str, Any],
    description: str = "",
) -> Dict[str, str]:
    """The provenance a report carries: what it is, and how it was made."""
    return {
        f"{FOOTER_PREFIX}project_name": project_name,
        f"{FOOTER_PREFIX}flavour": flavor,
        f"{FOOTER_PREFIX}description": description,
        f"{FOOTER_PREFIX}version": _version(),
        f"{FOOTER_PREFIX}created_at":
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        f"{FOOTER_PREFIX}loader": LOADER_NAME,
        f"{FOOTER_PREFIX}paths": json.dumps(list(paths)),
        f"{FOOTER_PREFIX}processing_stats": json.dumps(processing_stats),
        f"{FOOTER_PREFIX}privacy_summary": json.dumps(PRIVACY_SUMMARY),
        **({f"{FOOTER_PREFIX}base_dir": str(root.resolve())} if root is not None else {}),
    }


def write(report: Report, output: Path, *, root: Path, paths: Sequence[str],
          flavor: str, project_name: Optional[str] = None,
          omit_base_dir: bool = False, object_noun: Optional[str] = None) -> Path:
    """Write the report parquet: the rows, their descriptions, and the run's provenance."""
    output = Path(output)
    if output.suffix.lower() != ".parquet":
        output = output.with_suffix(".parquet")
    output.parent.mkdir(parents=True, exist_ok=True)

    # A batch where every object failed has nothing to write: a parquet with no columns at
    # all is 2 kB that no reader can open. The caller knows which objects failed.
    if not getattr(report.rows, "width", 0) or not report.rows.height:
        raise EmptyReport(
            f"nothing was measured, so there is no report to write to {output}")

    footer = footer_metadata(
        project_name=project_name or output.stem,
        flavor=flavor,
        root=None if omit_base_dir else root,
        paths=paths,
        processing_stats={
            "wall_s": round(report.seconds, 3),
            "n_objects": report.n_objects,
            "n_failed": len(report.failures),
            "seconds_per_object": {k: round(v, 1) for k, v in report.per_object_seconds.items()},
        },
    )
    if object_noun:
        footer[NOUN_KEY] = object_noun
    _write(report.rows, output, footer, noun=object_noun)
    logger.info("organella: wrote %s", output)
    return output


def read(report: Path) -> Tuple[Any, Dict[str, str]]:
    """A report back as (rows, footer). The counterpart of :func:`write`."""
    import polars as pl
    import pyarrow.parquet as pq

    report = Path(report)
    if not report.is_file():
        raise FileNotFoundError(f"no report at {report}")
    raw = pq.read_metadata(report).metadata or {}
    footer = {}
    for key, value in raw.items():
        name = key.decode()
        if name.startswith("ARROW:"):
            continue
        footer[name] = value.decode()
    return pl.read_parquet(report), footer


def column_descriptions(report: Path) -> Dict[str, str]:
    """The column descriptions a report carries, read from its footer."""
    _, footer = read(report)
    try:
        return dict(json.loads(footer.get(DESCRIPTIONS_KEY, "{}")))
    except json.JSONDecodeError:
        return {}


def noun_of(footer: Dict[str, str]) -> Optional[str]:
    """What this run called one measured thing, if it said."""
    return footer.get(NOUN_KEY) or None


def recolour(report: Path, colours: Dict[str, str]) -> int:
    """Write `colours` into an existing report's entity rows. Returns how many were coloured.

    Colouring is presentation and a run takes minutes, so a palette can be tried against a
    report that already exists. `process --colours` writes it the same way, and the run's
    footer is carried over untouched.
    """
    import polars as pl
    import pyarrow.parquet as pq

    report = Path(report)
    table = pq.read_table(report)
    frame = pl.from_arrow(table)
    if "entity_name" not in frame.columns:
        raise ValueError(f"{report} has no entity rows to colour")

    coloured = frame.with_columns(
        pl.col("entity_name").replace_strict(colours, default=None).alias("entity_colour")
    )
    _, kept = read(report)
    _write(coloured, report, kept)
    return int(coloured["entity_colour"].is_not_null().sum())


def _write(frame, dest: Path, footer: Dict[str, str], noun: Optional[str] = None) -> None:
    """One frame, one file: descriptions on the fields, provenance in the footer."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from organella.column_schema import descriptions_for

    table = frame.to_arrow()
    described = descriptions_for(table.column_names, noun=noun or noun_of(footer))
    fields = []
    for field in table.schema:
        description = described.get(field.name)
        if description:
            metadata = dict(field.metadata or {})
            metadata[b"description"] = description.encode()
            field = field.with_metadata(metadata)
        fields.append(field)
    # The same map in the footer as well: field metadata does not survive every reader.
    metadata = {**footer, DESCRIPTIONS_KEY: json.dumps(described, sort_keys=True)}
    table = table.cast(pa.schema(fields, metadata={k: v.encode() if isinstance(v, str) else v
                                                   for k, v in metadata.items()}))
    pq.write_table(table, dest, compression="zstd")


def _version() -> str:
    from organella import __version__

    return __version__
