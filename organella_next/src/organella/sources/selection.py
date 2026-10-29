from __future__ import annotations

from typing import Mapping

from organella.core import Structure, normalised


def selected(structures: Mapping[str, Structure], wanted: frozenset[str], object_mask: str | None) -> dict[str, Structure]:
    """Only the structures named (compared normalised), plus the object mask. Nothing named keeps them all."""
    if not wanted:
        return dict(structures)
    kept = {n: s for n, s in structures.items() if normalised(n) in wanted or n == object_mask}
    missing = wanted - {normalised(n) for n in structures}
    if missing:
        raise ValueError(f"--entities names {', '.join(sorted(missing))}, which this object does not have "
                         f"(it has {', '.join(sorted(structures))})")
    return kept
