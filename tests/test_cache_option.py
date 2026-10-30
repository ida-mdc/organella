from pathlib import Path

from click.testing import CliRunner

from organella.cli import main
from organella.sources import StoreSource, open_source


def test_the_cache_size_reaches_the_store_source(tmp_path):
    manifest = tmp_path / "cell.json"
    manifest.write_text("{}")
    assert open_source(manifest, cache_bytes=123).cache_bytes == 123
    assert isinstance(open_source(manifest), StoreSource)


def test_the_command_line_offers_the_cache_size():
    result = CliRunner().invoke(main, ["process", "--help"])
    assert "--cache-gb" in result.output
