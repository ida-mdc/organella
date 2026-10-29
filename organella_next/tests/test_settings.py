import pytest

from organella.app.settings import Settings, parse_geometry_as


def test_geometry_choices_parse_and_match_names_however_they_are_spelled():
    chosen = parse_geometry_as("ER=tube, Mito-Mem=mesh+skeleton ,vesicle=ellipsoid")
    assert chosen == {"er": "tube", "mito_mem": "mesh+skeleton", "vesicle": "ellipsoid"}
    settings = Settings(geometry_as=chosen)
    assert settings.forced_surface("ER") == "tube" and settings.forced_surface("mito-mem") == "mesh"
    assert settings.forced_surface("nucleus") is None


def test_a_tube_or_a_named_skeleton_asks_for_a_skeleton_and_nothing_else_does():
    settings = Settings(geometry_as=parse_geometry_as("er=tube,mito=mesh+skeleton,golgi=ellipsoid"), skeletons=frozenset({"ld"}))
    assert [settings.wants_skeleton(n) for n in ("er", "mito", "golgi", "ld", "lyso")] == [True, True, False, True, False]
    assert Settings(geometry_as=parse_geometry_as("mito=skeleton")).forced_surface("mito") is None   # draws nothing different


def test_a_name_beats_the_wildcard_whole_and_the_wildcard_covers_the_rest():
    settings = Settings(geometry_as=parse_geometry_as("*=mesh,vesicle=ellipsoid"))
    assert settings.forced_surface("vesicle") == "ellipsoid" and settings.forced_surface("anything") == "mesh"
    assert Settings(geometry_as=parse_geometry_as("*=tube")).wants_skeleton("whatever")


@pytest.mark.parametrize("raw,message", [("mito", "NAME=KIND"), ("mito=blob", "no kind called 'blob'"), ("mito=mesh+tube", "one")])
def test_a_malformed_choice_is_an_error_that_says_what_was_wrong(raw, message):
    with pytest.raises(ValueError, match=message):
        parse_geometry_as(raw)
