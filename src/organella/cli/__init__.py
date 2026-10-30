from __future__ import annotations

import logging

import click


@click.group()
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


from organella.cli import process, survey, tools  # noqa: E402,F401  (each registers its commands on ``main``)
