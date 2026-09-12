"""What touches what: the clustering the contacts section counts by, and the rule it rests on.

The clustering is JavaScript, and it is the report page's own - so it runs through node,
over rows shaped exactly like a report's, which exercises the reading of them at the same
time. The rest is about the report itself: a contact is a pair within one structure, and
every instance it names is one the report also measured on its own. Those hold whatever
reads the file, so they are checked against a real one.
"""

import duckdb
import pytest
from conftest import run_report_page

MASK = "pm"


# ── rows shaped like a report's, so the reading is exercised too ─────────────

def object_row(object_id, group="control"):
    return {"row_type": "object", "obs_level": 0, "object_id": object_id,
            "imported_path_short": group, "spatial_dims": 3, "object_mask_name": MASK,
            "object_volume_um3": 100.0}


def entity_row(object_id, entity, kind="label", group="control"):
    return {"row_type": "entity", "obs_level": 1, "object_id": object_id,
            "imported_path_short": group, "entity_name": entity, "entity_kind": kind,
            "instance_count": None if kind == "mask" else 1}


def instance_row(object_id, entity, label):
    return {"row_type": "instance", "obs_level": 2, "object_id": object_id,
            "instance_entity": entity, "instance_label": label,
            "instance_volume_um3": 1.0}


def contact_row(object_id, entity, label_a, label_b, gap=0.01):
    """One contact: two instances of one structure. There is no other kind."""
    return {"row_type": "contact", "obs_level": 2, "object_id": object_id,
            "contact_entity": entity, "contact_label_a": label_a,
            "contact_label_b": label_b, "contact_gap_um": gap}


def clusters_of(rows, *, object_id, entity="mito", gap=1.0):
    found = run_report_page({
        "rows": rows, "structure": entity,
        "clusters": [{"object": object_id, "entity": entity, "gap": gap}],
    })
    return found["clusters"][0]


def batch(instances, contacts, *, objects=("object_a",), entities=("mito",)):
    rows = [object_row(o) for o in objects]
    rows += [entity_row(o, e) for o in objects for e in entities]
    rows += [instance_row(*i) for i in instances]
    rows += [contact_row(*c) for c in contacts]
    return rows


# ── clustering ────────────────────────────────────────────────────────────────

def test_instances_that_touch_nothing_are_still_counted():
    rows = batch([("object_a", "mito", 1), ("object_a", "mito", 2)], [])

    found = clusters_of(rows, object_id="object_a")

    # Every instance is seeded, so an instance with no contact is a group of one rather
    # than missing. That is what lets "share of instances in contact" be a real fraction.
    assert found["instances"] == 2
    # A lone instance is not a group, so there are none of two or more.
    assert found["sizes"] == []
    assert found["grouped"] == 0


def test_a_chain_of_contacts_becomes_one_group():
    rows = batch([("object_a", "mito", label) for label in (1, 2, 3, 4)],
                 [("object_a", "mito", 1, 2), ("object_a", "mito", 2, 3)])

    found = clusters_of(rows, object_id="object_a")

    # 1-2-3 chain into one group of three; 4 stays alone and is not one.
    assert found["sizes"] == [3]
    assert found["instances"] == 4
    assert found["grouped"] == 3


def test_a_group_is_one_structure_and_another_structures_instances_stay_out():
    rows = batch([("object_a", "mito", 1), ("object_a", "mito", 2),
                  ("object_a", "granules", 1), ("object_a", "granules", 2)],
                 [("object_a", "mito", 1, 2)],
                 entities=("mito", "granules"))

    found = clusters_of(rows, object_id="object_a", entity="mito")

    # A contact is a pair within one structure, so the granules are not members of the
    # mito group whatever their label ids are - and they are not even counted, because the
    # share this section reports is a share of the structure it is about.
    assert found["instances"] == 2
    assert found["sizes"] == [2]


def test_the_same_label_id_in_two_objects_is_two_instances():
    rows = batch([("object_a", "mito", 1), ("object_b", "mito", 1)], [],
                 objects=("object_a", "object_b"))

    # Identity is object + label within the structure. Counted across objects, these two
    # would merge into one instance and an object would report half its population.
    assert clusters_of(rows, object_id="object_a")["instances"] == 1
    assert clusters_of(rows, object_id="object_b")["instances"] == 1


def test_a_contact_in_one_object_does_not_group_another_objects_instances():
    rows = batch([("object_a", "mito", 1), ("object_a", "mito", 2),
                  ("object_b", "mito", 1), ("object_b", "mito", 2)],
                 [("object_a", "mito", 1, 2)],
                 objects=("object_a", "object_b"))

    # One point per object is what the charts plot, so a group has to land on the object it
    # belongs to and nowhere else.
    assert clusters_of(rows, object_id="object_a")["sizes"] == [2]
    assert clusters_of(rows, object_id="object_b")["sizes"] == []


def test_object_names_with_awkward_characters_survive():
    name = 'a "1" b'
    rows = batch([(name, "mito", 1), (name, "mito", 2)],
                 [(name, "mito", 1, 2)], objects=(name,))

    assert clusters_of(rows, object_id=name)["sizes"] == [2]


def test_a_gap_wider_than_the_contact_is_what_links_it():
    rows = batch([("object_a", "mito", 1), ("object_a", "mito", 2)],
                 [("object_a", "mito", 1, 2, 0.2)])

    assert clusters_of(rows, object_id="object_a", gap=0.1)["sizes"] == []
    assert clusters_of(rows, object_id="object_a", gap=0.2)["sizes"] == [2]


# ── the rule the section rests on, in a real report ──────────────────────────

@pytest.fixture(scope="module")
def con(report_path):
    connection = duckdb.connect()
    connection.execute(
        f"CREATE VIEW report AS SELECT * FROM read_parquet('{report_path}')")
    return connection


def test_a_contact_is_between_two_different_instances_of_one_structure(con):
    """Touching a different structure is a distance, not a contact, and measured as one.

    So a contact row names one structure, both its label ids are instances of that
    structure, and they are two different instances.
    """
    structures = {s for (s,) in con.execute(
        "SELECT DISTINCT contact_entity FROM report WHERE row_type = 'contact'").fetchall()}
    labelled = {name for (name,) in con.execute(
        "SELECT DISTINCT entity_name FROM report WHERE entity_kind = 'label'").fetchall()}

    assert structures and structures <= labelled, "a mask has no instances to pair up"
    assert con.execute(
        """SELECT COUNT(*) FROM report
           WHERE row_type = 'contact' AND contact_label_a = contact_label_b"""
    ).fetchone()[0] == 0


def test_every_instance_a_contact_names_is_one_the_report_also_measured(con):
    """The seeding depends on it: a pair naming an instance with no row of its own would
    be an edge between two things the section never counted."""
    unmatched = con.execute("""
        SELECT COUNT(*) FROM report c
        WHERE c.row_type = 'contact' AND NOT EXISTS (
            SELECT 1 FROM report i
            WHERE i.row_type = 'instance' AND i.object_id = c.object_id
              AND i.instance_entity = c.contact_entity
              AND i.instance_label IN (c.contact_label_a, c.contact_label_b))""").fetchone()[0]

    assert unmatched == 0


def test_an_instance_can_be_either_half_of_a_pair(con):
    """Which is why the clustering unions both endpoints of every edge.

    Reading one column only would leave every instance that is always the second half of
    its pair counted as touching nothing.
    """
    both_sides = con.execute("""
        SELECT COUNT(*) FROM (
            SELECT object_id, contact_entity, contact_label_a AS label FROM report
            WHERE row_type = 'contact'
            UNION
            SELECT object_id, contact_entity, contact_label_b FROM report
            WHERE row_type = 'contact')""").fetchone()[0]
    one_side = con.execute("""
        SELECT COUNT(*) FROM (
            SELECT DISTINCT object_id, contact_entity, contact_label_a FROM report
            WHERE row_type = 'contact')""").fetchone()[0]

    assert one_side < both_sides


def test_the_contact_count_on_the_object_row_matches_its_pairs(con):
    """"How much is there" needs no query below the object row, so it has to agree."""
    total = con.execute(
        "SELECT COUNT(*) FROM report WHERE row_type = 'contact'").fetchone()[0]
    recorded = con.execute(
        "SELECT SUM(contact_count) FROM report WHERE row_type = 'object'").fetchone()[0]

    assert total == recorded


def test_every_distance_finds_its_instance(con):
    """The widening this page does is that join: object + structure + label.

    Instances and distances are separate kinds of row, so every distance has to find its
    instance. A missed join silently empties the proximity panels instead of being wrong.
    """
    unmatched = con.execute("""
        SELECT COUNT(*) FROM report d
        WHERE d.row_type = 'distance' AND NOT EXISTS (
            SELECT 1 FROM report i
            WHERE i.row_type = 'instance' AND i.object_id = d.object_id
              AND i.instance_entity = d.distance_entity
              AND i.instance_label = d.distance_label)""").fetchone()[0]

    assert unmatched == 0


def test_the_object_mask_is_a_distance_target(con):
    """The default axis of the reach panels: "does this group sit against the boundary"."""
    targets = {t for (t,) in con.execute(
        "SELECT DISTINCT distance_target FROM report WHERE row_type = 'distance'").fetchall()}
    mask = con.execute(
        "SELECT ANY_VALUE(object_mask_name) FROM report WHERE row_type = 'object'").fetchone()[0]

    assert mask in targets


# ── a group is a thing with a position, and gets asked what an instance is ──


def clusters_with_distances(rows, *, scope, entity="mito", gap=1.0):
    from conftest import run_report_page

    return run_report_page({
        "rows": rows, "structure": entity,
        "groupRows": [{"scope": scope, "entity": entity, "gap": gap}],
    })["groupRows"][0]


def distance_row(object_id, entity, label, target, value):
    return {"row_type": "distance", "obs_level": 2, "object_id": object_id,
            "distance_entity": entity, "distance_label": label,
            "distance_target": target, "distance_um": value}


def batch_with_distances(instances, contacts, *, objects=("object_a",)):
    """A batch whose instances carry distances, so its groups have somewhere to be.

    `instances` is `(object, entity, label, {target: distance})`, which is how the report
    holds it: the distances are rows of their own, one per instance x target.
    """
    rows = [object_row(o) for o in objects]
    for object_id in objects:
        rows.append(entity_row(object_id, "mito"))
        rows.append(entity_row(object_id, MASK, kind="mask"))
    for object_id, entity, label, distances in instances:
        rows.append(instance_row(object_id, entity, label))
        for target, value in distances.items():
            rows.append(distance_row(object_id, entity, label, target, value))
    rows += [contact_row(*c) for c in contacts]
    return rows


def test_a_groups_distance_is_its_closest_approach():
    """"This network reaches the membrane" is about the nearest any member gets, not the
    average and not the centre: a chain of ten can touch with one end."""
    rows = batch_with_distances(
        [("object_a", "mito", 1, {"pm": 0.9}),
         ("object_a", "mito", 2, {"pm": 0.2}),
         ("object_a", "mito", 3, {"pm": 0.5})],
        [("object_a", "mito", 1, 2), ("object_a", "mito", 2, 3)])

    built = clusters_with_distances(rows, scope=["object_a"])

    assert built["targets"] == ["distance_to_pm_um"]
    assert len(built["rows"]) == 1, "the three chain into one group"
    assert built["rows"][0]["size"] == 3
    assert built["rows"][0]["distance_to_pm_um"] == pytest.approx(0.2)


def test_a_group_carries_the_object_and_the_condition_it_belongs_to():
    """Otherwise the group panels cannot be faceted the way every other panel is."""
    rows = batch_with_distances(
        [("object_a", "mito", 1, {"pm": 0.4}), ("object_a", "mito", 2, {"pm": 0.6})],
        [("object_a", "mito", 1, 2)])

    group = clusters_with_distances(rows, scope=["object_a"])["rows"][0]

    assert group["object_id"] == "object_a"
    assert group["group_id"] == "control"


def test_a_group_of_one_is_not_a_group():
    """The panels are about groups of 2+, which is what the counts elsewhere call a group."""
    rows = batch_with_distances(
        [("object_a", "mito", 1, {"pm": 0.4}), ("object_a", "mito", 2, {"pm": 0.6})], [])

    assert clusters_with_distances(rows, scope=["object_a"])["rows"] == []


def test_a_target_no_member_was_measured_to_is_left_off_the_group():
    """Not filled with a zero, and not with the group's distance to something else."""
    rows = batch_with_distances(
        [("object_a", "mito", 1, {}), ("object_a", "mito", 2, {})],
        [("object_a", "mito", 1, 2)])

    built = clusters_with_distances(rows, scope=["object_a"])

    assert built["rows"] == [] or "distance_to_pm_um" not in built["rows"][0]


def test_groups_from_a_real_report_reach_where_their_members_do(report_path):
    """Every group's closest approach is one of its members' distances, and the smallest."""
    from conftest import report_rows

    rows = report_rows(report_path)
    scope = ["object_a", "object_b", "object_c", "object_d"]
    built = clusters_with_distances(rows, scope=scope, gap=10.0)

    assert built["rows"], "a wide gap groups everything"
    for group in built["rows"]:
        assert group["size"] >= 2
        for target in built["targets"]:
            if group.get(target) is None:
                continue
            assert group[target] >= 0


# ── when there is no group to describe ───────────────────────────────────────

def section_shown(rows):
    """Whether the groups section draws at all, for this report."""
    drawn = run_report_page({"rows": rows, "structure": "mito", "render": True})["render"]
    return "s-groups" in drawn["shown"]


def test_a_batch_of_whole_structure_masks_has_no_groups_section():
    """Nothing was segmented into instances, so nothing can touch anything of its own kind.

    The section would be an elaborate way of saying "one".
    """
    rows = [object_row("object_a"),
            entity_row("object_a", MASK, kind="mask"),
            entity_row("object_a", "liver", kind="mask")]

    assert not section_shown(rows)


def test_a_structure_with_one_instance_each_has_no_groups_section():
    """A group needs two of something. One instance per object cannot form one."""
    rows = batch([("object_a", "mito", 1), ("object_b", "mito", 1)], [],
                 objects=("object_a", "object_b"))

    assert not section_shown(rows)


def test_a_structure_with_instances_that_touch_does_have_one():
    rows = batch([("object_a", "mito", 1), ("object_a", "mito", 2)],
                 [("object_a", "mito", 1, 2)])

    assert section_shown(rows)
