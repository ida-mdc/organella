"""The command line on the two synthetic batches (``tests/synthetic.py``): every number in the report against the arrays
the batch was made from. CI's install job runs the same check on the installed wheel."""
import pytest

import synthetic


@pytest.mark.parametrize("dimension", ["2d", "3d"])
def test_a_synthetic_batch_measured_through_the_command_line_says_what_its_arrays_say(synthetic_reports, dimension):
    synthetic.check(synthetic_reports[dimension], int(dimension[0]))


def test_a_plane_reads_in_pixels_and_a_volume_in_voxels(synthetic_reports):
    import json

    from organella.report.file import read_report
    said = {d: json.loads(read_report(synthetic_reports[d])[1]["organella_column_descriptions"]) for d in ("2d", "3d")}
    assert "image" in said["2d"]["instance_centroid_y_um"] and "pixel" in said["2d"]["distance_um"]
    assert "volume" in said["3d"]["instance_centroid_y_um"] and "voxel" in said["3d"]["distance_um"]
