# label-anatomy: reference

The spatial anatomy of segmented objects. It reads and measures them itself - four
measurers, one per depth of the report it produces - and ships the standalone page that
reads the result: one HTML file that loads a report in the browser and draws the whole
thing from it. [README.md](README.md) is the short path through a run; this is the
reference for the data model, the report and every configuration knob.

It began as a [PixelPatrol](https://pixelpatrol.app/) flavour and no longer depends on it;
see [Why an object is never split](#why-an-object-is-never-split).

An *object* is one segmented thing measured as a whole: a cell, an organoid, a nucleus, a
tissue block. `--object-noun cell` makes the report say so - see
[What the report calls things](#what-the-report-calls-things). It is given as a folder holding a source image, one mask that bounds the object,
and any number of label/mask volumes inside it. Everything is measured relative to that one
bounding mask: the region is cropped to it, and every polarity metric is measured from its
centroid.

Which mask that is, you say: `--object-mask NAME` is never inferred. It decides
the origin of every distance and polarity in the report, so guessing it from file names
would mean a regex quietly choosing what the numbers are relative to. `dry-run` lists the
masks in each folder, which is where the name comes from.

Leaving it out is allowed and means what it says: nothing bounds the object. The entities
are measured where they lie, with no clipping and no cropping, and the object's own extent
is not written at all rather than filled with something invented.

Polarity survives that, because an origin needs a middle and not a boundary: with no mask
named it is the centroid of everything segmented. A run of bare labels therefore still gets
`polar_dist_um` and its directions, and the 3D view still has a direction to explode along.

Reports are stamped with the `object anatomy` flavour, which the report page shows in the
strip at the foot of it.

## Data model

An object is a *folder* of volumes that must be measured together. `is_object_dir`
recognises one by what is inside it, so the directory is the unit and the TIFFs inside are
never objects of their own. Each object is read as one stack and becomes a fixed set of rows:

| Anatomy concept | in the report |
| --- | --- |
| one object folder | one stack, entity volumes along `C` (`CZYX`, or `CYX` in 2D) |
| whole object | one row at `obs_level=0` (`row_type=object`) |
| one entity (organelle) | one row at `obs_level=1` (`row_type=entity`), named by `entity_name` |
| one instance | one row at `obs_level=2`, `row_type=instance` |
| one instance → entity distance | one row at `obs_level=2`, `row_type=distance` |
| one contact (two instances of one structure) | one row at `obs_level=2`, `row_type=contact` |
| experimental group | a `-p` import path (`imported_path_short`) |

One row per entity is what makes `entity_name` a groupable column with scalar metrics that
can be plotted directly. A per-entity measurement sees only its own entity, so anything
measured *between* entities (a distance, a contact) is measured against the whole object
instead.

What it measures goes to rows of its own rather than onto the object row, and `row_type` says
which kind a row is. The same data as list columns on the object row - which is how this
package started - meant an object's whole instance and contact tables were loaded to answer
any question at all; as rows of their own they are read column-pruned, and a question about
them is plain SQL rather than several `unnest()` calls that have to stay row-aligned.

A deep row carries `object_id` and what was measured, and nothing else. The group, the
provenance and the cohort filter belong to the object, so a reader joins back to its object
row; that join is also what makes a deep row honour whatever is being grouped by.

**Instances**, one row per instance:

```sql
SELECT object_id, instance_entity, instance_label, instance_volume_um3, instance_sphericity
FROM report WHERE row_type = 'instance'
```

**Distances**, long format, one row per instance x target entity, because target
names come from the data, so a column per target could not be declared up front:

```sql
SELECT object_id, distance_entity, distance_label, distance_target, distance_um
FROM report WHERE row_type = 'distance'
```

**Contacts**, the touching pairs, thresholded on `gap_um` interactively:

```sql
SELECT object_id, contact_entity, contact_label_a, contact_label_b, contact_gap_um
FROM report WHERE row_type = 'contact' AND contact_gap_um <= 0.1
```

Both instances of a pair are of the **same structure**, and only structures that have
instances take part: two mitochondria touching is a mitochondrial network, where a
mitochondrion touching the nucleus is a *distance* to the nucleus and is one of the distance
rows above. A mask has no instances to pair up at all. That is also why one `contact_entity`
column is enough for a pair.

The object row keeps the counts - `instance_count` per structure and `contact_count` - so
"how much is there" needs no query below it.

Distances and gaps are measured voxel centre to voxel centre, so structures sharing a
face read *one voxel step* rather than zero, and with anisotropic voxels the smallest
non-overlapping reading depends on direction. Zero means genuine overlap.

### Reading data that was laid out for something else

An object folder is a source image plus its entity volumes, and the naming rule is
`<prefix>_<name>_label|labels|mask`, where the prefix is the source image's basename. The
prefix is taken off the *front*, and the entity name is everything that remains, so a name
may have underscores in it: `s0011_rib_left_11_mask` is the entity `rib_left_11`. Splitting
at the last underscore instead - which is what this used to do - read that file as the
entity `11`, which collided with `rib_right_11` and dropped one of the two with nothing
said.

Published segmentations are not going to be renamed to suit a reader, so three things about
them are read as they come.

**Formats.** `readers.py` is the only place a file becomes an array. TIFF goes through
tifffile; NIfTI, NRRD, MetaImage and the rest of what ITK reads go through SimpleITK, which
is already a dependency. Both return `(Z, Y, X)`, the order everything downstream measures
in - SimpleITK's *spacing* is `(x, y, z)` and is reversed once, at the boundary, so nothing
below has to remember which convention it holds. Headers are read without the pixels, which
is what keeps `dry-run` a header read: 6 ms against 500 ms on a 42-megavoxel NIfTI.

A NIfTI header carries a voxel size where a TIFF usually does not. Its spacing is taken as
millimetres, because that is the NIfTI convention and its unit field is very often 0
(*unknown*) - it is 0 throughout TotalSegmentator. `voxel_size_source` records
`image-header-mm` rather than `tiff-metadata` so the report says which it was, and
`--voxel-size-um` overrides either.

**Entities in a subfolder.** A `segmentations/`, `masks/` or `labels/` folder beside the
source image holds entities named by nothing but the structure. There is no prefix to
strip, so the file name is the name.

**What such a file is** cannot come from its name, and is not guessed from it: it is read
off the array. More than one distinct non-zero value is a label volume, one is a mask. That
is a fact about the data rather than a claim about intent, which is why it does not
contradict the object mask never being inferred - and the entity *named* as the object mask
is a mask by definition, whatever it holds, because that is what naming it means. Discovery
records such an entity as `auto` and `dry-run` prints it as `auto`; the kind is settled when
the pixels are read. An entity key is `<kind>:<name>`, but the kind in a key is an identity
and not a claim - a mask promoted to a label keeps its key - so nothing rebuilds a key from
a kind it assumes. `key_for_name` is how the object mask's channel is found.

**An object may be described rather than stored.** A folder holding one `source.json` and no
images is an object too: the manifest names a chunked store, the arrays inside it, and the
window to read.

```json
{
  "store": "s3://janelia-cosem-datasets/jrc_hela-2/jrc_hela-2.n5",
  "scale": "s0",
  "crop": "3712:4224,256:768,5760:6272",
  "source": "em/fibsem-uint16",
  "entities": { "mito": "labels/mito_seg", "er": "labels/er_seg" },
  "voxel_size_um": [0.00524, 0.004, 0.004]
}
```

`scale` is appended to every array path, so the same manifest reads a different resolution by
changing one word. `crop` is in voxels *at that scale*, and is the point of the whole thing: a
chunked array is read block by block, so a window costs the blocks it overlaps and nothing
else. A 512³ crop of OpenOrganelle's 122-gigavoxel `jrc_hela-2` is **8 of its 1248 chunks -
0.64%, read in under five seconds**, which is how a part of a cell is measured at full 4 nm
resolution without the cell ever being downloaded. Reading the whole volume to get there would
mean 122 gigavoxels for a measurement that needs 134 million.

Everything else is unchanged: the entities arrive as `auto` and learn their kind from their
content, `--entities` selects, groups and `--resume` work, and `dry-run` reads array metadata
rather than pixels - so a remote dataset can be surveyed before a byte of it is fetched. The
manifest is also better provenance than a folder of TIFFs, because it records exactly which
store, scale and window a report came from, in about a kilobyte.

`voxel_size_um` is optional; without it the size comes from the store's own metadata, which
records nanometres - either the `multiscales` transform on the parent group (per scale level,
so the one that matters for a downsampled array) or `pixelResolution` on the array.
`voxel_size_source` says which was used: `manifest` or `store-metadata-nm`.

Reading a store needs `zarr` and `s3fs`, which are the `remote` extra rather than
dependencies - measuring files on disk needs neither. zarr is pinned below 3 because zarr 3
dropped N5 support.

**Selecting, and one volume that is many structures.** TotalSegmentator ships 117 masks per
subject; stacking all of them is 118 channels of the whole field of view, which projects
46 GB for one subject where seven entities project 2.4. `--entities` is therefore not a
convenience but what makes such an object measurable, and an unknown name is an error
listing what the folder has. `--label-map` splits one volume whose ids each mean a different
structure into an entity per id; only the ids it names become entities, and when
`--entities` is given too, only those are ever materialised. Which entity to split is asked
for rather than guessed when a folder has more than one label volume.

### What a surface is stored as

A mesh per instance does not scale. One 512³ crop of `jrc_hela-2` produced 2,698 meshes and
4.8 million vertices; a whole macrophage at the same settings holds 34,110 instances. But
most of those are not shapes that need a mesh, so a surface is one of three kinds and
`surface_kind` says which:

| kind | when | stored as |
| --- | --- | --- |
| `ellipsoid` | round and compact | 60 bytes: centre, radii, a 3×3 of axes |
| `tube` | elongated, with a centre line | the skeleton polyline plus a radius per node |
| `mesh` | everything else | marching cubes, as before |

**No measurement depends on this.** Volume, surface area, sphericity and aspect ratio all
come from ITK's Crofton estimator on the voxels (`analysis/shapes.py`), and nothing in the
measurers reads a surface. That is what makes approximating one legitimate: an ellipsoid
changes no number in the report, only the picture.

Both parametric kinds are free of new computation. The ellipsoid is
`GetEquivalentEllipsoidDiameter` / `GetPrincipalAxes` / `GetCentroid` on the
`LabelShapeStatisticsImageFilter` that already runs for every instance; the tube's radii are
kimimaro's own, measured while skeletonising and previously discarded. A parametric instance
also never enters the meshing pool, so the run gets shorter as well as smaller.

`choose_surface` in `analysis/primitives.py` is the whole of the policy - pure, and the
thresholds are its only constants. `--geometry-as NAME=KIND` overrides it: `mesh`,
`ellipsoid` or `tube` for the surface and `skeleton` for a centre line over it, combined with
`+`, because a surface and an overlay are different questions - `mito=mesh+skeleton` is the
alpha-cell case. Naming only `skeleton` forces no surface, so a filament that now has a
centre line can be drawn as the tube it is.

A tube is a polyline rather than a deformed base cylinder deliberately: a polyline can differ
in length, curvature and how many branches it has, none of which a fixed mesh can be
deformed into. And a sheet has no parametric family at all - ER reads about 0.05 sphericity -
so it stays a mesh, which is why the kinds are three representations for three topologies
rather than one trick applied everywhere.

The page holds one builder per kind in `SURFACE_BUILDERS`, each returning the same positions
and indices the scene already merges, so there is no second render path - the tessellation
density is a viewer-side choice. A test compares the writer's kinds with the page's, so a
kind nothing can draw fails the suite rather than vanishing silently in a browser.

### What a mesh costs, once it is the only thing left

The kinds above take care of the many small instances. What remains is a handful of large
ones, and on one real object they are almost all of the bytes: of 128 MB of surfaces, ER
alone was 82 MB and the top 14 instances were 94%.

A mesh payload is **exactly 30 bytes per vertex** - 6 for the quantised position and 24 for
the faces, because a closed triangle mesh has about twice as many faces as vertices and each
was three `uint32`. So two things bound it, neither needing a new dependency:

**Indices are `uint16` below 65,536 vertices**, which on that object was 151 of 158 meshes:
40% off each of them. The width is *derived* from the vertex count rather than recorded, so
the writer, the report page, the test mirror and the Blender importer all work it out from
the header they have already read - a width flag would be a second source of truth for
something the first already determines.

**`--mesh-max-vertices` bounds the worst case.** A decimation fraction cannot: at 0.5 the ER
sheet was still 2.87 million vertices where a vesicle was 57. The budget is what makes a
file size predictable, so it defaults to 200,000 rather than being opt-in.

Deliberately *not* a mesh codec. Draco or meshopt would give 10-15x, but both need a WASM
decoder, and the report page is one standalone HTML file with no build step that you can
drop a parquet onto - embedding a decoder or fetching one from a CDN would cost exactly the
property that makes the page droppable. Quantisation and a budget keep that intact.

### Extracting the surface, and what a dual method does not buy

`--mesh-surface-method` chooses between marching cubes, which puts a vertex on every
crossing *edge*, and surface nets, which puts one per crossing *cell* at the average of that
cell's crossings.

The expectation was that a dual method would give far fewer, better-placed vertices, and on
a real ER sheet it did not: 5,584,555 against 5,575,347, **0.16% apart**. A surface has about
as many crossing cells as crossing edges, so the counts land together. What surface nets does
buy is smoothness - on a sphere of known radius its vertices scatter 30% less (sd 0.142
against 0.203) - for 66% more time.

It also couples the axes. A dual vertex is the mean of all twelve of its cell's crossings, so
what happens along z moves the vertex in x; marching cubes places each vertex on its own
edge and cannot. On a small ball at 5x anisotropy that shifted the x extent by 7%, and alpha
cells are sampled 0.1 x 0.02 x 0.02 µm. So marching cubes stays the default and surface nets
is there for the cases where a smoother surface is worth the time - isotropic data, and
structures whose staircases show.

Neither is what makes geometry small: that is `--mesh-max-vertices` and the surface kinds.

### 2D and 3D

An object is a volume or a plane, and each is measured by the metrics that mean something
for it. Reading an object folder takes the source image's dimensionality and builds a `CZYX`
stack for a volume or a `CYX` one for a plane, never a volume one voxel deep, and
`spatial_dims` on every row says which it was.

| Measured | 3D object | 2D object |
| --- | --- | --- |
| extent | `volume_um3`, `total_volume_um3`, `object_volume_um3` | `area_um2`, `total_area_um2`, `object_area_um2` |
| boundary | `surface_area_um2` (ITK Crofton) | `perimeter_um` (ITK Crofton) |
| roundness | `sphericity` | `circularity` |
| direction from the object centre | `polar_az_deg`, `polar_el_deg`, `polar_nz/ny/nx` | `polar_angle_deg`, `polar_ny/nx` |
| geometry beside the report | marching-cubes meshes | closed outline loops |
| skeletons | TEASAR curve skeletons (kimimaro) | thinned medial axis (`skimage`) |

The boundary is ITK's Crofton estimator, not a count of voxel faces: it is built for smooth
surfaces, which is what a segmented organelle has. A sphere of radius 1 µm sampled at
0.1 × 0.05 × 0.05 µm reads

| | volume | surface | roundness |
| --- | --- | --- | --- |
| Anatomy | 4.155 µm³ | 12.48 µm² | sphericity 1.00 |
| analytic | 4.189 µm³ | 12.57 µm² | 1 |

**Sphericity is the surface area of the equal-volume sphere divided by the measured surface
area, so 1 is a perfect ball and nothing is rounder than one.** A few percent above 1 is the
estimator coming in short, which it does on instances only a handful of voxels across;
`tests/test_reference_agreement.py` pins the sphere at 1.00 ± 0.02 and a cube's faces at
10–15% short, which is the other side of the same trade. Faceted shapes read well below 1: a
cube 0.92, a thin slab or a square rod about 0.6. The report says this under the chart of it,
because a value over 1 is the first thing anyone asks about.

Everything else is measured identically and keeps its name: instance counts, the PCA aspect
ratio, `branches` / `length_um` / `tortuosity`, every `distance_*` column, and the contact
contacts. Distances and gaps are in whole sample steps in both cases.

The other dimensionality's columns are declared but never filled, so one report can hold
both kinds of object. A report with only one kind simply has no columns for the other,
since a column no row filled is dropped on the way out. `--voxel-size-um` follows the same rule:
`z,y,x` for a volume, `y,x` for a plane, and a mismatch with the images is refused rather
than reinterpreted.

## Install (development)

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e . pytest
```

Everything below runs in that activated environment; `.venv/bin/<command>` works too, for
a one-off without activating.

That install runs the whole suite. Reading a report needs nothing more than a browser:
`label-anatomy page` prints the standalone page, which has no build step and no
install of its own.

Nothing here depends on PixelPatrol. It used to, at two edges - the parquet writer and the
schema catalogue that put a description on every column - and both are now `report_io.py`
and `column_schema.py`, about two hundred lines between them. The *file* is unchanged: the
footer keys still start with `pp_`, so a report written before and one written after are the
same kind of file and either reads in the same page. What went away was a git-pinned
dependency on an unreleased branch, and the plugin entry points that advertised our schema
to a catalogue nothing was consulting any more.

## Run

`dry-run` first, to see what the folders hold and which masks you can choose from; then
`process` with the mask that bounds the object:

```bash
label-anatomy dry-run experiment/
# ... masks   nucleus, pm
# Pick the mask that bounds each object and pass it as --object-mask: nucleus, pm

label-anatomy process experiment/ -o report.parquet --object-mask pm \
    -p control -p treated
label-anatomy view report.parquet    # serves the report and opens the page on it
```

Naming a mask a folder does not have is an error rather than a fallback, and the message
lists the masks that folder actually has.

`view` is a convenience, not a requirement: the report is one parquet and the page that
reads it is one HTML file, so `label-anatomy page` and a browser are enough. What
`view` adds is the geometry, which a page cannot read off your disk on its own.

On a real dataset (eight vEM alpha objects, 2.5–10.5 GB stacked, five entities each)
`process` prints:

```
8 object folder(s); 3 worker(s) (largest object needs ~17.9 GB each)
```

### Resuming

Nothing is written until the batch finishes, so a run that died late used to lose every
object it had already measured - hours, on a batch of vEM cells. Each object's rows now go
to `<output>_parts/<object>.parquet` as it completes, and `--resume` reuses them instead of
measuring those objects again. The directory is removed once the report is written, so it
only holds anything after a run that failed. Parts are written by the parent under a
temporary name and renamed, so a part is either whole or absent; one that is unreadable or
empty is measured again rather than trusted.

With `--reuse-geometry` as well, resuming an interrupted `--with-mesh` run costs neither the
measuring nor the meshing of the objects that finished.

### Why an object is never split

`process` measures each object whole, and that is the reason this package has a pipeline of
its own rather than running on a framework that chunks. The only axis an object can be split
along is `C`, and a chunk holding two of five entities can answer almost nothing: a distance
is *to* another entity and a contact is *between* two of them. A split object loses its
instances, contacts and geometry - and where a refused chunk is only a warning, the run
still reports success while measuring nothing.

So each object is loaded whole and measured in a pool sized by what an object actually costs
(measured peak on a 133-megavoxel five-entity object: 4.4 GB). One object that fails is
reported by name and the batch continues; the command exits non-zero if any did.

It is also why nothing under `model/`, `analysis/` or `measure/` knows what a report file
is: an object, an entity of it and a measurement of one are this package's own types, and a
measurer is a plain class you hand an object to.

```text
model/       what the things are: ObjectStack, EntityVolume, ObjectMeasurement, Report,
             and the row_type vocabulary. Data and accessors, nothing else
analysis/    the measuring itself, in arrays: shapes, distances, gaps, skeletons, meshes.
             Knows nothing about objects, rows or files
measure/     what to record about an object, and reading one off disk: one module per
             depth of the report, plus loading.py and discovery.py
pipeline/    runs the measurers over a batch: batch.py is analyse(), objects.py
             measures one, pool.py runs them wide, parts.py is --resume, table.py
             assembles the rows
cli.py       the command line, with config.py for the options it sets
```

Each layer uses only the ones above it, and the *file* is written at two edges:

| | where |
| --- | --- |
| the parquet, its field descriptions and its provenance footer | `report_io.py`, the only module that writes one |
| what every column means | `column_schema.py`, gathered from the measurers that fill them |

A report is self-describing twice over, and the second copy has a job: every column carries
its description in the parquet's *field* metadata, and the whole map goes in the footer as
well, under `anatomy_column_descriptions`. The footer copy is what the page reads, because a
browser cannot reach field metadata - DuckDB drops it and `parquet_schema()` does not expose
it - and it is why the sentence under a chart is the same sentence as the one in the file
rather than a second version kept somewhere else. `tests/test_metric_help.py` fails if a
column reaches a report with nothing describing it.

Reading a report needs no Python at all: the page is one HTML file whose only dependencies
are a browser and three CDN scripts, so a report can be read by anyone it is handed to.

### Speed

Measured on one real object (133 megavoxels, five entities, 6340 instances), with
distances, histograms, contacts and polarity spread all on:

```
load          6s     instances    39s     contacts    16s     meshes   133s
```

Those meshing and instance figures predate the parallel meshing and the allocation work
below, and have not been re-measured on that object since.

Three things got it there, and the first is worth knowing about if you measure volumes of
this size yourself:

- **`scipy.ndimage.minimum` is unusable at this scale**: 54 s per call on a 197-megavoxel
  object, and 57 s when asked for only 50 of the labels, because its cost follows the volume
  and label count rather than the foreground. Each entity's foreground is indexed once
  instead (positions sorted by label id), after which a measurement is a gather plus
  `np.minimum.reduceat`: 0.01 s, same answers.
- **`--skeleton-entities mito`**: skeletonising dominates everything else (103 s of a
  2-minute object), and most of it is usually wasted: a granule's skeleton is one branch the
  length of its diameter. Naming just the filaments took that object from 55 s to 36 s -
  which is why it is **opt-in**: nothing is skeletonised unless a run names it, so branches,
  length and tortuosity are measured for the structures they mean something for and no
  others.
- **Nothing is measured twice per object.** Skeletons, contacts, ITK's shape statistics and
  `regionprops` are computed once and shared, so `--with-mesh` does not repeat for its
  geometry what the instance table already measured.
- **Objects run in parallel**, as many at once as their measured peak allows, and **an
  object's instances are meshed in parallel** within that. Two levels, because the object
  pool is sized by memory rather than by cores: on a batch of large objects it is three
  workers on a machine with far more cores than that, and meshing is the longest part of a
  run. Measured on 22 cores with 8 mesh workers: 1.7x at 400 instances, 2.7x at 900.

Two things keep peak memory down, both worth knowing if you profile a run: entity volumes
are stacked in the narrowest integer type their label ids need (usually `uint16`, halving
a `float32`/`int32` segmentation), and the instance measurer holds **one** whole-volume
distance transform at a time, reducing it over every entity's labels with one pass before
freeing it.

Skip the expensive parts with `--no-instances` / `--no-contacts`.

## Geometry for the 3D views and Blender

Geometry never enters the parquet, because meshes would multiply the size of the report every
stats query loads: one real object is 36 MB of them against a 1 MB report. It goes to
one `geometry.parquet` per object instead. Either during processing:

```bash
label-anatomy process experiment/ -o report.parquet --object-mask pm --with-mesh \
  --mesh-smooth-sigma 1 --mesh-step-size 1 --mesh-level 0.05
# → report.parquet  +  report_meshes/<object>/geometry.parquet
```

or afterwards, when you have a report already and want to re-mesh with other settings:

```bash
label-anatomy mesh experiment/ -o geometry/ --object-mask pm \
  --mesh-step-size 1 --skeleton-entities mito
```

Each file holds one row per label instance (with a `mesh` blob, and a `skeleton` blob for
the structures `--skeleton-entities` named), one per whole-structure mask, and the contact
edge list the 3D view groups by. The payload is the quantised vertex/index container
the standalone prototype used, stored raw with parquet's own zstd doing the compressing,
`geometry_to_blender.py` decodes it, and so does `decodePayload` in the report page.

Being parquet rather than one long CSV is what makes it *queryable*, which is the whole
mechanism behind the 3D views: DuckDB, WASM in the browser, filters by object, structure and
metric and returns only the rows about to be drawn. The twelve roundest granules cost twelve
meshes, not an object's worth. The header counts (`mesh_vertices`, `mesh_faces`) are columns
of their own, so a view can budget its draw calls before transferring any geometry at all.

The object row carries `mesh_geometry_file`, the path this was written to; it is the only
thing the geometry writer adds to the table, and it is how the page finds the geometry.

Meshing is the most expensive thing here (it skeletonises again for the gallery), which
is why it is opt-in rather than part of every run. It is also why `--reuse-geometry` exists:
it keeps the `geometry.parquet` an object already has instead of writing it again, so a batch
interrupted partway is finished in minutes rather than hours. A file that is missing, empty or
truncated is written again rather than trusted - a run killed mid-write is exactly the case
the flag is for.

## The report

A report is read by one standalone page,
`src/label_anatomy/report/anatomy_report.html`. Open it, drop a `report.parquet` on
it, and every chart is drawn in the browser from that file: DuckDB-WASM parses the parquet,
Plotly draws, three.js does the 3D. Nothing is uploaded, no server runs, and the file
depends on nothing beside it, so it can be copied anywhere or published as a page and
handed round with a link. `label-anatomy page` prints where the installed copy is.

It is read like a paper rather than scanned like a dashboard, which is why the headings are
questions. Five sections, in the order the questions come:

| Section | Answers |
| --- | --- |
| **What is in this report** | one row per object, one column per structure: instance counts, ✓ for a whole-structure mask, and **missing** where an object was not segmented the same way as its neighbours, plus the object mask's own extent so objects are comparable by size. Then the total extent of each structure, one panel each. Read it first: objects are segmented by hand, so one is often missing a structure or has a nucleus split into three labels, and a distribution drawn across objects hides that. It also says when a batch mixes 2D and 3D objects, whose extents are not comparable, and when only some objects were measured for instances or contacts |
| **The object itself** | one object as it was segmented, before any distribution: orbit, structure toggles, colour by structure / by a metric / by contact group at a live gap, and an explode slider along each instance's own direction from the object centre. A 2D object is drawn as outlines |
| **Structure by structure** | one structure at a time, and every question about it in one place: how big and what shape each instance is; **which instances those are**, as meshes you can click; how far each sits from every other structure; whether it is close to two of them at once, one panel per pair, side by side; how much of its *body* lies near a structure, over every voxel; and a free explorer for any two measurements |
| **Groups of one structure** | instances of one structure that touch each other, chained into groups at a live gap: how large they get, what share of instances reach another - out of *every* instance, including the ones touching nothing, which is what makes it a share - then the same two questions the instances got, asked of the groups: how far each group is from every other structure, and whether it reaches two at once. A group's distance is its *closest approach*, the nearest any member gets, which is what "this network touches the membrane" means. And between them, the baseline: whether a group is closer than a group of its size would be anyway |
| **The files behind it** | what was read, and what was measured of each whole structure |

The instance gallery is inside **Structure by structure** rather than a section of its own,
directly under the distributions it explains: a sphericity outlier in the box above is a
thumbnail below it, and clicking it shows the two granules the segmentation merged. An
instance with a skeleton is drawn see-through so the skeleton shows.

One filter strip at the top applies to all of it - group, object, structure, and whether
charts are faceted by object or by group - so every section is about the same subset.

### What the report calls things

The columns are named `object_*`, and stay that way: renaming them per run would make two
reports of the same kind unjoinable. The *word the report uses* is a different question, and
it is not a preference - it depends on whether anything bounds the thing:

- **with** `--object-mask`, there is a body with a centroid and an extent of its own. It is a
  cell, a specimen, an object. Unset, the report says **object**
- **without** one, there is no such body: a source image with labels lying in it, no centre
  to measure a direction from and no extent of its own - and the columns for both are already
  absent from the file. Unset, the report says **dataset**, and it drops the boundary
  sentence rather than filling it with a placeholder

`--object-noun cell` names it, in either case. Give an irregular plural as
`nucleus/nuclei`; otherwise the plural is the singular plus an *s*, and the article follows
the word, because a hard-coded "a" gives you "a object" the moment the word changes.

It reaches the page two ways, both written by the run: the footer key
`anatomy_object_noun`, which the page reads for its own prose, and the column descriptions,
which are substituted where the file is written - so "Distance in µm from the **cell**
centre to the instance centroid" is the same sentence in the page, in `pyarrow`, and in a
notebook. Nothing else changes: no column, no measurement, and no cached geometry, which is
why the noun is deliberately not part of the settings fingerprint.

### Two ways to draw a population, and one test

Every "how big / how far" panel, whether it is about instances or about groups of them,
goes through one function, so **Charts** in the top bar switches all of them at once:

| | reads |
| --- | --- |
| **Distributions** | a box per facet: where the middle is and how spread out. Unbeatable for "are these bigger than those" |
| **Histograms** | a binned shape per facet, on one grid shared by all of them, each drawn as a share of its own population. Shows a second mode, or a hard floor, that a box hides completely |

The grid is shared because per-facet grids put the same value in differently placed bins
and the shapes stop being comparable - the same rule the per-voxel panels bin by. Each
facet is a percentage of *its own* count, so a condition with three times the instances
does not simply draw three times as tall. Bin count is the square root of n, capped at 24:
below five bins the shape is the binning, above thirty it is the noise. The outline is
drawn on bin *edges* and closed at zero at both ends - plotted against centres, every bar
sits half a bin to the left of the data in it.

A facet with a single value is drawn as a bar either way: a box over one observation is a
line, and reads as a distribution that is not there. A panel where *every* facet is one
constant value has no shape to bin, and falls back to boxes rather than drawing nothing.

**Significance** is one checkbox and nothing else - a two-sided **Mann-Whitney U** between
whatever the panels are faceted by, drawn when it is asked for. It is deliberately not tied
to the faceting: between conditions the comparison is a result, between two objects of one
condition it is two samples of the same thing, and hiding the second case would be the page
deciding which question the reader is allowed to ask. Both say what they compared - the
hover on a bracket and the line under a panel both name the two facets - so a p-value
between two objects is legible as exactly that.

On boxes it is a bracket per pair, up to six; past that, and on histograms, whose facets
share one x axis and so have no positions to bracket between, the same comparisons go under
the panel as text. The test is exact where both samples are small and nothing ties, and the
tie-corrected normal approximation otherwise, which is the split scipy makes. Exact matters
at this scale rather than being pedantry: at four objects against four the approximation
moves a p across 0.05. `tests/test_significance.py` pins every branch against
`scipy.stats.mannwhitneyu`.

Two panels are deliberately left without a bracket, and say so on the page: group sizes,
and the baseline scores below - in both, the values within one object are not independent
observations of a condition. The block that *is* one number per object, the share of
instances reaching another, is the one a difference between conditions is tested on.

### The group-reach baseline

One number in the report is not read off the data, and it is here because of a fact about
taking a minimum: **a group of ten reaches closer to anything than a group of two**,
whatever either is made of, because the closest of ten draws is closer than the closest of
two. A group's distance therefore cannot be compared across sizes, and plotting it against
size measures the arithmetic rather than the biology.

So each group is scored against the groups of *its own size* that the same structure in the
same object could have formed. A group is a chain of one structure, so that pool is simply
that object's instances of it: of all C(n, k) subsets of size k, what share does this group
beat by reaching closer? **50%** is what chance gives at any size, and the panel draws a
line there.

Exact rather than simulated. P(a random k-subset stays beyond `reach`) is
C(n − closer − equal, k) / C(n, k) - every member drawn from the instances further out -
and ties are split evenly, which is what keeps the score flat under the null instead of
drifting up wherever distances repeat. In whole voxel steps, they repeat a lot. The
binomials are in logs throughout, because C(8000, 4000) has no floating-point value at all.

The concrete median distance behind each box sits under the panel, because a score does not
say how close "closer" actually is - and the raw distances are the panel above it, so both
readings are on the page. `tests/test_group_baseline.py` checks the share against
enumerating every subset, and that it averages exactly one half over every group that could
have formed, at four different sizes.

### Colours per structure

What each structure should look like is a property of the study, not of the tool, so it is a
settings file:

```json
{
  "mito": "#d62728",
  "er": "#2ca02c",
  "granules": "#ff7f0e"
}
```

```bash
# during a run
label-anatomy process experiment/ -o report.parquet --object-mask pm --colours palette.json

# or onto a report that already exists, in about a second
label-anatomy colours report.parquet palette.json
```

Both write the same column through the same call, so a palette can be tried and changed
without measuring anything again: the second form re-reads no pixels, and carries the footer
the run was written with over untouched. Run it again with a different file to change your
mind.

Colours land on the entity rows as `entity_colour`, so they travel with the report: hand the
parquet to someone and it arrives already coloured, with no second file to pass around and
nothing to configure. The page reads that one column everywhere, so a swatch in the table, a
box in a chart and a mesh in the 3D view are the same colour.

Hex only, `#rrggbb` or `#rgb`, expanded and lower-cased on the way in. A structure the file
does not name keeps its place in the built-in palette, so one file can name the structures a
project cares about and leave the rest alone, and a name the batch does not have is simply
unused. A file that is not valid JSON, or gives something that is not a colour, stops the run
and says which structure it was.

### Geometry, and how it reaches the page

The two 3D parts read the geometry sidecar rather than the report, and say so instead of
sitting empty when they have none. It arrives one of two ways:

- **the folder**, dropped on the page or chosen with **Add geometry**. Each
  `geometry.parquet` is registered into the same DuckDB the report is read through, so one
  query spans report and geometry. This needs no server at all, `file://` included
- **a base URL**, `?geometry=<url>`, read over HTTP. `label-anatomy view` is this
  automated: it serves the report, its geometry and the page from one localhost origin -
  one origin because a browser will not fetch the geometry from another - and opens the page
  pointed at both

Either way the queries are the point of the sidecar being parquet: asking for the twelve
roundest granules costs twelve meshes, not an object's worth. The header counts
(`mesh_vertices`, `mesh_faces`) are columns of their own, so a section can budget its draw
calls before transferring any geometry at all.

three.js is imported lazily from the jsDelivr CDN, on the first 3D draw and not before. An
object is drawn as one merged geometry with per-vertex colours, so 8,000 instances cost one
draw call.

That one merged mesh casts shadows onto itself, which is what tells you which granule is in
front of which; a low ambient term, a hemisphere light and fog across the object's own depth
do the rest. The gallery leaves shadows off, since a thumbnail holds one instance and there
are hundreds of them.

The key light stands 35° off to one side of the camera. Fixed in the world it would leave
whole sides of the object unlit, so turning the back to the front would show you a dark
shape; riding with the camera at an offset, the surface you are looking at is always lit
while the light still moves relative to the object, so the shadows travel across it as you
orbit. Head-on would flatten everything. The light intensities sum to about one on the
brightest face, because past that a lit surface clips towards white and takes its hue with
it.

### How the sections read the report

The page takes the table apart once, into the four populations the sections ask about, and
two things happen on the way. A deep row carries only `object_id`, so the group, the
provenance and the structure's kind are joined back on from the row above it - that join is
also what makes a deep row honour the filter. And distances arrive *long*, one row per
instance × target, while every question here is per instance: "close to the nucleus **and**
the membrane" reads two columns of one row, not two rows. So they are widened onto the
instance they were measured from, one column per target.

Reach curves are drawn from quantiles: 51 vertices instead of 8,000 points, with the same
reading at any *n*. Contact groups need a union-find over the pairs at the slider's gap,
seeded with every instance so a lone instance counts as a group of one. Where an object
holds one or two instances of a single-copy structure, the split by object is what fails
rather than the encoding, so the instances are pooled into one curve and the panel says so.

The pairs are one panel each, side by side. They were a lower-triangle subplot matrix, which
is compact and unreadable: which pair a panel was about had to be worked out from where it
sat, and at five structures each was 148px wide with no axis of its own.

A structure's name comes out of the segmentation, not out of this page, so every heading and
sentence that uses one marks it: "How far is each `mito` from other structures?" reads like
a typo until you know that `mito` is a folder's own word. The mark is painted with a
box-shadow rather than padding, so a following "?" sits where it would have anyway. And a
unit inside an uppercased table head opts out of the transform, because uppercasing `µm³`
gives `ΜM³` - `µ` becomes a capital Mu, and it reads as an M.

### Testing a page nothing imports

It is one HTML file with no build step, which is what makes it droppable and also what makes
it easy to break silently: JavaScript has no compiler and nothing imports the page, so a
typo reaches a browser and nowhere else. `tests/report_page_check.mjs` lifts its `<script>`
out and runs it in node, and `tests/test_report_page.py` drives that:

- the report read into the page, checked against SQL over the same parquet - every row kind
  lands in its population, every distance finds its instance, every deep row picks up its
  group
- the maths the panels draw: the share curves behind the pair panels, the binning of a
  population onto one shared grid, the re-binning of the per-voxel histograms, the binned
  trends, and the clustering
- the binary container `analysis/meshes.py` writes, decoded at an unaligned offset the way
  it arrives from Arrow, and the index arithmetic of the merge
- every query the geometry sections build, run through DuckDB against geometry a real run
  wrote, so a binder error is a failing test rather than a message in a console
- **every section actually drawn**, against a stub DOM with Plotly recording what it was
  asked for - including which panels got brackets, and that switching to histograms keeps
  the same panels and moves the comparison into the text under them. A typo in the overview
  once emptied the whole report while reporting it as a failure to load the parquet; this is
  what stands between that and a reader
- the significance test itself, against `scipy.stats.mannwhitneyu` at both branches, rather
  than against a second implementation of the same idea written beside it
- the group-reach baseline against brute force: every subset enumerated, and the score
  shown to average exactly one half under the null, which is what makes 50% the line

## Configuration

A measurer is constructed with no arguments - it is handed an object and nothing else - so
the analysis knobs travel as environment variables, each set by the CLI flag of the same
name:

| Variable | Default | CLI flag |
| --- | --- | --- |
| `LABEL_ANATOMY_OBJECT_MASK` | unset (nothing bounds the object) | `--object-mask NAME` |
| `LABEL_ANATOMY_VOXEL_SIZE_UM` | inferred from the image header | `--voxel-size-um z,y,x` (3D) or `y,x` (2D) |
| `LABEL_ANATOMY_NO_CLIP` | `0` (entities are clipped to the object mask) | `--no-clip` |
| `LABEL_ANATOMY_AUTO_LABEL_MASKS` | `0` | `--auto-label-masks` |
| `LABEL_ANATOMY_ENTITIES` | unset: every entity the folder has | `--entities liver,spleen` |
| `LABEL_ANATOMY_LABEL_MAP` | unset: a label volume is one entity | `--label-map FILE` |
| `LABEL_ANATOMY_LABEL_MAP_ENTITY` | unset: the folder's one label entity | `--label-map-entity NAME` |
| `LABEL_ANATOMY_ENTITY_COLOURS` | built-in palette | `--colours FILE` |
| `LABEL_ANATOMY_MAX_SKELETON_VOXELS` | `500000` | `--max-skeleton-voxels` |
| `LABEL_ANATOMY_GEOMETRY_AS` | unset: decided from each shape, nothing skeletonised | `--geometry-as mito=mesh+skeleton` |
| `LABEL_ANATOMY_EDT_THREADS` | `0` (all cores) | none |
| `LABEL_ANATOMY_NUM_THREADS` | `1` (objects already run in parallel) | `--num-threads` |
| `LABEL_ANATOMY_CONTACT_MAX_UM` | `0.5` | `--contact-max-um` |
| `LABEL_ANATOMY_POLARITY_SPREAD` | `0` | `--polarity-spread` |
| `LABEL_ANATOMY_DISTANCE_HISTOGRAMS` | `0` | `--distance-histograms` |
| `LABEL_ANATOMY_MESH_DIR` | unset (no geometry) | `--with-mesh` / `--mesh-dir` |
| `LABEL_ANATOMY_MESH_MAX_VERTICES` | `200000` (0 = no cap) | `--mesh-max-vertices` |
| `LABEL_ANATOMY_MESH_SURFACE_METHOD` | `marching-cubes` | `--mesh-surface-method` |
| `LABEL_ANATOMY_MESH_SMOOTH_SIGMA` | `0.7` | `--mesh-smooth-sigma` |
| `LABEL_ANATOMY_MESH_STEP_SIZE` | `2` | `--mesh-step-size` |
| `LABEL_ANATOMY_MESH_TARGET_REDUCTION` | `0.8` | `--mesh-target-reduction` |
| `LABEL_ANATOMY_MESH_LEVEL` | `0` | `--mesh-level` |
| `LABEL_ANATOMY_MESH_WORKERS` | the cores the object pool is not using | `--mesh-workers` |
| `LABEL_ANATOMY_REUSE_GEOMETRY` | `0` | `--reuse-geometry` |

`label-anatomy process` sets these from its own flags, so you only need the variables when
driving the pipeline yourself. `--with-contacts` has no equivalent: contacts are measured by
default, and skipped with `--no-contacts`.

## Tests

```bash
pytest
```

`tests/synthetic.py` builds a small grouped batch of synthetic objects (membrane,
nucleus, mitochondria) with voxel size in the TIFF metadata; it is also runnable as a
script to produce a dataset to try the CLI on:

```bash
python tests/synthetic.py /tmp/objects
```

Parts of the suite need **node**: the report page is JavaScript, and rather than mirroring
its logic in Python the suite runs the page itself
(`tests/report_page_check.mjs`, driven by `tests/test_report_page.py`). Without node those
tests fail rather than skip - a mirror can be right while the page is wrong, which is the
whole reason they run the real thing.
