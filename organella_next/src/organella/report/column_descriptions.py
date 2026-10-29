"""What each report column means, shown under a chart and in the column help. Words say "object"; a run that names
its object (a cell) has them read that way."""
from __future__ import annotations

import re

COLUMNS: dict[str, str] = {
    'area_um2': (
        'Area of the whole structure in µm², where it was segmented as one thing.'
    ),
    'aspect_ratio_major_minor': (
        'Longest principal axis ÷ shortest, for the whole structure. 1 is a sphere, or a disc in a plane; higher is '
        'more elongated, flatter, or both.'
    ),
    'baseline_excluded': (
        'Structures left out of the region, from --baseline-exclude, comma separated. The target itself is always out '
        'of it, since its distance to itself is zero. Null where nothing else was excluded.'
    ),
    'baseline_hist_counts': (
        'Sample counts over that range, as a JSON array of fixed-width bins: the shape of the chance distribution, to '
        'draw a measured distribution against.'
    ),
    'baseline_hist_max_um': (
        "Upper bound of the chance histogram's range: the run's reach. The last bin also holds every sample farther "
        'than that.'
    ),
    'baseline_hist_min_um': (
        "Lower bound of the chance histogram's range, always 0."
    ),
    'baseline_mean_um': (
        "Mean distance in µm to this structure over every sample of the region, with distances past the run's reach "
        'counted as the reach: how far from it a point of the object sits on average, up to the reach.'
    ),
    'baseline_median_um': (
        'Median distance in µm to this structure over the region - the distance half the object is closer than. This '
        'is the number a measured distance is read against: a structure whose instances sit at 0.3 µm from the '
        "membrane where half the object is within 0.9 µm is closer to it than the object's shape alone would put "
        'them; one at 0.9 µm is exactly as close as anything would be. Taken from a fine histogram of the samples, so '
        'it is exact to a small fraction of the range.'
    ),
    'baseline_target': (
        'Structure the chance distribution on this row is measured to. One row per structure measured to, for the '
        'object as a whole.'
    ),
    'baseline_voxels': (
        'How many samples of the object the chance distribution was taken over.'
    ),
    'channel_names': (
        'The structures stacked along C for this object, in order - one channel each, which is how an object is read '
        'as a single stack.'
    ),
    'circularity': (
        'Perimeter of the equal-area disc divided by the measured perimeter. 1 is a perfect disc; a few percent above '
        'it is the boundary estimator coming in short on a very small instance, and an elongated or ragged outline '
        'reads below.'
    ),
    'coarse_factor': (
        'Set when the object was measured a window at a time (--windowed): how many voxels wide the coarse view was '
        'that the whole-object measurements ran on - the chance distributions, how deep each structure sits, the '
        "shape of each whole-structure mask, and any distance past the window's reach. 1 means they ran at full "
        'resolution. Null for an object measured whole.'
    ),
    'contact_count': (
        'Number of instance pairs of one structure within the recorded gap threshold; on the object row.'
    ),
    'contact_entity': (
        'Structure both instances of this pair belong to; a pair is always within one structure.'
    ),
    'contact_gap_um': (
        'Surface-to-surface gap of the pair in µm, measured in whole voxel steps, so instances sharing a face read '
        'one voxel step.'
    ),
    'contact_label_a': (
        'Label id of the first instance of the pair, the lower of the two.'
    ),
    'contact_label_b': (
        'Label id of the second instance of the pair.'
    ),
    'distance_beyond_reach': (
        "True where the instance is farther from the target than the run's reach: its distance is then not measured "
        'past the reach and reads as the reach.'
    ),
    'distance_end_max_um': (
        "Largest distance in µm from one of this instance's skeleton tips to the target structure: the tip that sits "
        'furthest from it. With the usual two tips, the pair says whether one end is against the structure while the '
        "other is not. Read up to the run's reach, like distance_um."
    ),
    'distance_end_min_um': (
        "Smallest distance in µm from one of this instance's skeleton tips to the target structure: how close its "
        '*end* gets, where distance_um is how close any part of it gets. Null for a structure with no curve skeleton '
        "(--skeletons), and for an instance whose skeleton is a closed loop and so has no tips. Read up to the run's "
        'reach, like distance_um.'
    ),
    'distance_entity': (
        'Structure of the instance this distance row is about.'
    ),
    'distance_hist_counts': (
        'Per-instance voxel counts over the histogram range, as a JSON array of fixed-width bins.'
    ),
    'distance_hist_max_um': (
        'Upper bound of the histogram range, shared by every instance of this structure measured to this target.'
    ),
    'distance_hist_min_um': (
        'Lower bound of the histogram range, shared by every instance of this structure measured to this target.'
    ),
    'distance_label': (
        'Label id of the instance being measured.'
    ),
    'distance_mean_um': (
        "Mean distance in µm over the instance's voxels. Null unless distance histograms are enabled."
    ),
    'distance_target': (
        'Structure measured to; for the object mask this is the distance to the object boundary.'
    ),
    'distance_um': (
        "Smallest distance in µm from this instance's voxels to the nearest voxel of the target structure. 0 means "
        "they overlap. Exact up to the run's reach; past it the value reads as the reach itself and "
        'distance_beyond_reach says so.'
    ),
    'entity_colour': (
        'Colour this structure is drawn in, as #rrggbb, from the settings file given to --colours (or `organella '
        'colours` afterwards). Null where the file did not name it, and the report falls back to its own palette.'
    ),
    'entity_file_bytes': (
        "Size on disk of each structure's file, in channel_names order."
    ),
    'entity_files': (
        'File each structure was read from, in channel_names order.'
    ),
    'entity_kind': (
        "'mask' where the structure was segmented as one whole thing, 'label' where it was segmented into separate "
        'instances.'
    ),
    'entity_kinds': (
        "Kind ('label' or 'mask') of each structure, in channel_names order."
    ),
    'entity_name': (
        'Name of the structure this row measures, as the segmentation called it.'
    ),
    'file_extension': (
        'Lower-cased extension of the source image, without the leading dot.'
    ),
    'file_name': (
        'Name of the TIFF this structure was read from.'
    ),
    'file_size_bytes': (
        'Size on disk of that TIFF, in bytes.'
    ),
    'imported_path_short': (
        'The experimental group this object was imported as, from the -p path it was found under. This is the axis a '
        'difference between conditions is read along.'
    ),
    'instance_area_um2': (
        'Area of this instance in µm².'
    ),
    'instance_aspect_ratio_major_minor': (
        'Longest principal axis ÷ shortest. 1 is a sphere, or a disc in a plane; higher is more elongated, flatter, '
        'or both.'
    ),
    'instance_branches': (
        "Branches in this instance's curve skeleton. Only measured for the structures a run named with --skeletons."
    ),
    'instance_centroid_x_um': (
        "Position of this instance's centroid along X in µm, from the origin of the volume that was measured."
    ),
    'instance_centroid_y_um': (
        "Position of this instance's centroid along Y in µm, from the origin of the volume that was measured."
    ),
    'instance_centroid_z_um': (
        "Position of this instance's centroid along Z in µm, from the origin of the volume that was measured."
    ),
    'instance_circularity': (
        'Perimeter of the equal-area disc divided by the measured perimeter. 1 is a perfect disc; a few percent above '
        'it is the boundary estimator coming in short on a very small instance, and an elongated or ragged outline '
        'reads below.'
    ),
    'instance_count': (
        'How many separate instances of this structure the object holds. Null for a whole-structure mask, which is '
        'one thing; on the object row, summed over the structures that have instances.'
    ),
    'instance_distance_to_closest_same_type_um': (
        "Centre-to-centre distance in µm to the nearest other instance of the same structure - how crowded this one's "
        'neighbourhood is.'
    ),
    'instance_entity': (
        'Structure the instance on this row belongs to.'
    ),
    'instance_label': (
        'Label id of this instance within its structure.'
    ),
    'instance_length_um': (
        "Total length of this instance's curve skeleton in µm - the length along it, not end to end."
    ),
    'instance_perimeter_um': (
        "Perimeter of this instance in µm, by ITK's Crofton estimator: right on a smooth outline, and short on a "
        'straight axis-aligned edge.'
    ),
    'instance_polar_angle_deg': (
        'Which way this instance lies from the object centre, in degrees.'
    ),
    'instance_polar_az_deg': (
        'Which way this instance lies from the object centre, around the Z axis, in degrees.'
    ),
    'instance_polar_dist_um': (
        'Distance in µm from the object centre to the instance centroid.'
    ),
    'instance_polar_el_deg': (
        'How far above or below the object centre this instance lies, in degrees.'
    ),
    'instance_polar_nx': (
        'X component of the unit vector from the object centre to the instance centroid.'
    ),
    'instance_polar_ny': (
        'Y component of the unit vector from the object centre to the instance centroid.'
    ),
    'instance_polar_nz': (
        "Z component of the unit vector from the object centre to this instance's centroid."
    ),
    'instance_polar_spread_deg': (
        "Angular spread in degrees of the instance's voxels as seen from the object centre: how much of a direction "
        'range it covers. Null unless polarity spread is enabled.'
    ),
    'instance_sphericity': (
        'Surface area of the equal-volume sphere divided by the measured surface area. 1 is a perfect sphere and '
        'nothing is rounder than one; a few percent above 1 is the boundary estimator coming in short, which it does '
        'on instances a handful of voxels across.'
    ),
    'instance_surface_area_um2': (
        "Surface area of this instance in µm², by ITK's Crofton estimator: right on a smooth surface, and about 10% "
        'short on a flat axis-aligned face.'
    ),
    'instance_tortuosity': (
        'How far from straight the skeleton runs: length along it ÷ the straight line between its ends, weighted by '
        'branch length. 1 is straight.'
    ),
    'instance_volume_um3': (
        'Volume of this instance in µm³.'
    ),
    'mesh_geometry_file': (
        "Where this object's meshes and skeletons were written. The 3D widgets query this file for the instances they "
        'draw; null when the object produced no geometry.'
    ),
    'modification_date': (
        'When the source image was last modified.'
    ),
    'n_entities': (
        'How many structures were read for this object and stacked along C.'
    ),
    'num_pixels': (
        'Number of samples in the analysed region of this object.'
    ),
    'object_area_um2': (
        'Area in µm² enclosed by the object mask.'
    ),
    'object_center_x_um': (
        'X coordinate in µm of the object centre.'
    ),
    'object_center_y_um': (
        'Y coordinate in µm of the object centre.'
    ),
    'object_center_z_um': (
        'Z coordinate in µm of the object centre, the origin every polarity metric is measured from: the centroid of '
        'the object mask where one was named, and of everything segmented where none was. Null for a 2D object.'
    ),
    'object_id': (
        'Name of the object folder the structure volumes were read from.'
    ),
    'object_mask_name': (
        'Name of the structure whose mask bounds the object; every measurement is relative to it.'
    ),
    'object_shape': (
        "Extent of the analysed region after cropping to the object mask's bounding box, in the spatial axes of "
        'dim_order: (Z, Y, X) for a volume, (Y, X) for a plane.'
    ),
    'object_volume_um3': (
        'Volume in µm³ enclosed by the object mask.'
    ),
    'obs_level': (
        'How deep in the report this row sits: 0 is the whole object, 1 is one structure of it, 2 is one instance, '
        'distance or contact below that.'
    ),
    'path': (
        'Path of the object folder, relative to the batch root.'
    ),
    'perimeter_um': (
        "Perimeter of the whole structure in µm, by ITK's Crofton estimator: right on a smooth outline, and short on "
        'a straight axis-aligned edge.'
    ),
    'polar_angle_deg': (
        'Which way this structure lies from the object centre, in degrees. A plane has one angle where a volume has '
        'an azimuth and an elevation.'
    ),
    'polar_az_deg': (
        'Which way this structure lies from the object centre, around the Z axis, in degrees.'
    ),
    'polar_depth_um': (
        "How deep inside the object mask this structure's centre sits, in µm: the distance from its centroid to the "
        'boundary. The comparable way to say where a structure sits, since objects differ in size - where '
        "polar_dist_um says how far from the centre it is, which is only readable against that object's own extent. 0 "
        "where the centroid falls outside the mask, which a horseshoe-shaped structure's can; null without a bounding "
        'mask.'
    ),
    'polar_dist_um': (
        "Distance in µm from the object centre to this structure's centroid."
    ),
    'polar_el_deg': (
        'How far above or below the object centre this structure lies, in degrees.'
    ),
    'polar_nx': (
        "X component of the unit vector from the object centre to this structure's centroid."
    ),
    'polar_ny': (
        "Y component of the unit vector from the object centre to this structure's centroid."
    ),
    'polar_nz': (
        "Z component of the unit vector from the object centre to this structure's centroid."
    ),
    'row_type': (
        "What this row is: 'object' (obs_level 0), 'entity' (obs_level 1, one per structure), or one of 'instance', "
        "'distance' and 'contact' below them."
    ),
    'size_bytes': (
        'Size on disk of everything read for this object, in bytes.'
    ),
    'spatial_dims': (
        "3 for a volume, 2 for a plane. Which size and shape metrics the object's rows carry follows from it."
    ),
    'sphericity': (
        'Surface area of the equal-volume sphere divided by the measured surface area. 1 is a perfect sphere and '
        'nothing is rounder than one; a few percent above 1 is the boundary estimator coming in short, which it does '
        'on instances a handful of voxels across.'
    ),
    'surface_area_um2': (
        "Surface area of the whole structure in µm², by ITK's Crofton estimator: right on a smooth surface, and about "
        '10% short on a flat axis-aligned face.'
    ),
    'total_area_um2': (
        'How much of this structure there is altogether, in µm²: every instance added together, or the whole thing '
        'where it was segmented as one.'
    ),
    'total_volume_um3': (
        'How much of this structure there is altogether, in µm³: every instance added together, or the whole thing '
        'where it was segmented as one.'
    ),
    'volume_um3': (
        'Volume of the whole structure in µm³, where it was segmented as one thing.'
    ),
    'voxel_size_source': (
        "Where the voxel size came from: 'tiff-metadata' or 'config'."
    ),
}

# Columns named by a pattern: one entry covers every axis.
PATTERNS: list[tuple[str, str]] = [
    ('^pixel_size_[zyx]$',
     'Physical size of one sample along this axis, in µm. Every measurement in the report is in µm because of it, so it is worth checking: it comes from the TIFF metadata unless --voxel-size-um overrode it, and voxel_size_source says which.'),
    ('^size_[zyx]$',
     'Extent of the analysed region along this axis, in samples.'),
]


def describe(column: str) -> str:
    """What a column means: the exact entry, else a pattern, else nothing."""
    if column in COLUMNS:
        return COLUMNS[column]
    return next((text for pattern, text in PATTERNS if re.match(pattern, column)), "")


def plural_of(noun: str) -> str:
    """The plural of a ``singular`` or ``singular/plural`` noun the run supplied: ``cell`` gives ``cells``,
    ``nucleus/nuclei`` gives ``nuclei``, because guessing a plural from English spelling is a losing game."""
    singular, _, plural = noun.partition("/")
    return plural or (singular if singular.endswith("s") else singular + "s")


def with_noun(description: str, noun: str | None) -> str:
    """A description with "object" replaced by what the run calls one, whole words only, so ``object_id`` is left alone."""
    if not noun:
        return description
    return re.sub(r"\bobject\b", noun.partition("/")[0], re.sub(r"\bobjects\b", plural_of(noun), description))


def descriptions_for(columns, noun: str | None = None) -> dict[str, str]:
    """The description of each of these columns that has one."""
    return {c: with_noun(describe(c), noun) for c in columns if describe(c)}

