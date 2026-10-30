import pytest

from organella.app.settings import Settings, parse_geometry_as


def test_geometry_choices_parse_and_match_names_however_they_are_spelled():
    chosen = parse_geometry_as("ER=tube, Mito-Mem=mesh+skeleton ,vesicle=ellipsoid")
    assert chosen == {"er": "tube", "mito_mem": "mesh+skeleton", "vesicle": "ellipsoid"}
    settings = Settings(geometry_as=chosen)
    assert settings.forced_surface("ER") == "tube" and settings.forced_surface("mito-mem") == "mesh"
    assert settings.forced_surface("nucleus") is None


def test_skeletons_are_asked_for_by_name_or_by_drawing_a_tube_and_a_name_beats_the_wildcard():
    settings = Settings(geometry_as=parse_geometry_as("er=tube,mito=mesh+skeleton,golgi=ellipsoid"), skeletons=frozenset({"ld"}))
    assert [settings.wants_skeleton(n) for n in ("er", "mito", "golgi", "ld", "lyso")] == [True, True, False, True, False]
    assert Settings(geometry_as=parse_geometry_as("mito=skeleton")).forced_surface("mito") is None   # draws nothing different
    wild = Settings(geometry_as=parse_geometry_as("*=mesh,vesicle=ellipsoid"))
    assert wild.forced_surface("vesicle") == "ellipsoid" and wild.forced_surface("anything") == "mesh"
    assert Settings(geometry_as=parse_geometry_as("*=tube")).wants_skeleton("whatever")


@pytest.mark.parametrize("raw,message", [("mito", "NAME=KIND"), ("mito=blob", "no kind called 'blob'"), ("mito=mesh+tube", "one")])
def test_a_malformed_choice_is_an_error_that_says_what_was_wrong(raw, message):
    with pytest.raises(ValueError, match=message):
        parse_geometry_as(raw)


def test_a_command_line_option_named_like_a_setting_sets_it_and_a_no_flag_switches_one_off():
    from organella.cli.options import settings_from
    given = settings_from({"reach_um": 2.5, "skeletons": frozenset({"er"}), "no_clip": True, "groups": ()}, geometry=True)
    assert (given.reach_um, given.skeletons, given.clip_to_object_mask, given.keep_skeletons) == (2.5, frozenset({"er"}), False, True)
    assert settings_from({}, geometry=False) == Settings()
