# label-anatomy: spatial analysis of segmented objects

label-anatomy analyses TIFF segmentations (labels + masks), 2D or 3D, for one object or
many, and produces a single report you read in one standalone page: distributions,
distances, contacts, and the objects themselves in 3D.

An **object** is one segmented thing measured as a whole, given as a folder: a source
image, one mask that bounds the object, and the label/mask volumes inside it. In this
project an object is a cell and its bounding mask is the plasma membrane, so `--object-mask pm`.
Nothing about the tool assumes that: the mask is named explicitly, never guessed, and
may be left out entirely - then the entities are measured where they lie.

Objects can be volumes or planes. A 2D object is measured as a plane, so area, perimeter,
circularity and one polarity angle; a 3D one as a volume, so volume, surface area,
sphericity, azimuth and elevation. Nothing is measured with the wrong formula and no plane
is padded into a volume one voxel deep; see the package README's
[2D and 3D](REFERENCE.md#2d-and-3d) for the full column split.

It reads and measures objects itself, and ships the report page that reads the result.
[REFERENCE.md](REFERENCE.md) is the reference for the data model, the report and every
configuration knob; this page is the short path through a run.

## Workflow

1. Organise your TIFFs in one of the input layouts below.
2. `label-anatomy dry-run` to check what will be analysed, and what was ignored.
3. `label-anatomy process` to write `report.parquet` (and, with `--with-mesh`, the geometry).
4. Open the report page and drop the parquet on it - or `label-anatomy view` to do
   that for you, geometry included. `label-anatomy colours report.parquet palette.json`
   sets a colour per structure at any point. In the page, **Charts** switches every panel
   between boxes and histograms, and **Significance** puts Mann-Whitney brackets between
   whatever the charts are faceted by.
5. Optionally import an object's geometry into Blender.

## Try it

One command, no clone and no node:

```bash
uv pip install "label-anatomy @ git+https://github.com/betaseg/cellsketch-v2.git@main"
```

```bash
label-anatomy dry-run experiment/
label-anatomy process experiment/ -o report.parquet --object-mask pm \
    -p control -p treated --with-mesh
```

Then read it. Open **<https://betaseg.github.io/cellsketch-v2/>** and drop
`report.parquet` on it. That page is the whole report: it parses the parquet in the browser,
so nothing is uploaded and no server runs. `label-anatomy page` prints the copy that
came with your install, if you would rather open a local file.

**The 3D sections need the geometry too.** It never enters the report - it is a separate
`geometry.parquet` per object, beside it - and a page cannot read a path on your disk. So
drop the `report_meshes` folder in as well, or press **Add geometry**, and the object and
its instances appear. One command does both for you:

```bash
label-anatomy view report.parquet
```

which serves the report, its geometry and the page from one localhost origin and opens it.

Nothing but the analysis stack comes with it: the report is written and read by this
package, so there is no unreleased dependency to pin and nothing to build.

## Install for development

The same install, editable, from a checkout:

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e .
```

Every command below assumes that activated environment; without it, reach into the venv
directly instead (`.venv/bin/label-anatomy …`).

### Working on the report page

The page is one file with no build step, so there is nothing to install and nothing to
watch: edit
[`src/label_anatomy/report/anatomy_report.html`](src/label_anatomy/report/anatomy_report.html)
and reload the browser.

```bash
label-anatomy view report.parquet     # serves that same file, plus the geometry
```

It has no compiler and nothing imports it, so the suite runs it through node instead - the
way a report is read into it, the maths its panels draw, the queries its 3D sections build,
and every section drawn against a stub DOM:

```bash
.venv/bin/python -m pytest tests/test_report_page.py
```

## Analysing a batch (`process`)

```bash
label-anatomy process experiment/ -o report.parquet --object-mask pm \
    -p control -p treated --with-mesh
```

`-p` names the subdirectories to import as groups; that grouping becomes the default
comparison in every chart. `--with-mesh` is opt-in because meshing (and skeletonising
again for the overlay) is the most expensive part of a run.

Useful flags, see `label-anatomy process --help` for the rest:

- `--voxel-size-um z,y,x`: manual voxel size, otherwise inferred from the source TIFF
- `--object-mask NAME`: the mask that bounds each object, e.g. `pm`. Everything is measured
  relative to it, so it is never inferred; `dry-run` lists the masks each folder has. Leave
  it out and nothing bounds the object: no clipping, no cropping, and no extent of its own.
  Polarity is still measured, from the centre of everything segmented rather than from a
  mask's centroid
- `--no-clip`: measure outside the object mask too. Entities are clipped to it by
  default, because that is what naming a bounding mask means: a field of view often
  holds neighbouring cells, and on one real alpha cell 3678 of 8800 granules lay
  entirely outside the plasma membrane. Pass this for data already confined to the
  object, or when truncating what straddles the boundary is worse than including it
- `--colours palette.json`: a colour per structure, e.g. `{"mito": "#d62728"}`. It lands in
  the report, so every chart draws that structure the same and a shared report arrives
  coloured; unnamed structures keep the built-in palette. `label-anatomy colours
  report.parquet palette.json` does it to a report you already have, in about a second
- `--skeleton-entities mito,er`: skeletonise these structures, and no others. Nothing
  named means none: branches, length and tortuosity mean something for a filament and
  nothing for a granule, whose skeleton is one branch the length of its diameter - and
  skeletonising is the most expensive thing in a run, so it is not done on the off chance
- `--entities liver,spleen`: measure only these, plus the object mask. Everything a folder
  has is measured when this is left out, which for a published segmentation can be far more
  than a question needs: each entity is another full-size channel of the stack, so a
  TotalSegmentator subject projects 46 GB with all 117 structures and 2.4 GB with seven
- `--label-map FILE`: JSON of `{"1": "liver", "2": "spleen"}`, splitting one volume whose
  ids each mean a different structure into an entity per id. Only the ids it names become
  entities, and with `--entities` only those are ever built. `--label-map-entity NAME` says
  which entity to split when a folder has more than one label volume - an error rather than
  a guess if you leave it out
- `--no-instances` / `--no-contacts`: skip the expensive per-instance work
- `--contact-max-um T`: largest surface-to-surface gap recorded as a contact. A contact is
  a pair of instances of *one* structure; touching a different structure is a distance,
  and is measured as one
- `--mesh-smooth-sigma` / `--mesh-step-size` / `--mesh-target-reduction` / `--mesh-level`
- `--mesh-workers N`: processes meshing one object's instances. The default divides the
  machine between the two levels of parallelism, so objects and instances do not each
  claim every core
- `--reuse-geometry`: keep the `geometry.parquet` an object already has rather than meshing
  it again. Meshing is most of a long run, so this is how a batch that died partway is
  finished in minutes. A file that is missing, empty or truncated is written again
- `--resume`: skip objects an interrupted run already measured. Each object's rows go to
  `<output>_parts/` the moment it finishes and are removed once the report is written, so
  a run that dies late no longer costs the objects it had already done

## Checking your input first (`dry-run`)

Before a long batch, check that every object has the files you expect:

```bash
label-anatomy dry-run experiment/
```

It reads TIFF headers only, so no analysis, no output, seconds even for a large batch. It
prints, per object: the source image with its size, the label and mask entities found
(`*` marks the object mask), and anything that looks wrong. Then a cross-object summary
of which entities are **missing in which objects**, and a suggested `--max-workers` for the
run. Exit code is `1` if any object cannot be analysed.

```text
control/cell_b
  source  sample_b.tif   [12.4 MB stacked]
  labels  mito
  masks   nucleus, pm*
  warn    ignored readme_overlay.tif: not <prefix>_<name>_label|labels|mask

===== 3 object folder(s) =====  (* = object mask)
  label:mito               2/3   ← missing in some objects
  mask:pm                  3/3
```

An uneven batch is worth catching here, and again in the report: its first section shows
the same thing for the report you actually produced, and says which object is missing what
before anything is pooled across the batch.

## Input data layout

Entity files must match `<prefix>_<name>_label.tif` (or `_labels.tif`) and
`<prefix>_<name>_mask.tif`, where `<prefix>` is the source image basename:

```text
my_cell/
  sample.tif
  sample_mito_label.tif
  sample_nucleus_mask.tif
  sample_membrane_mask.tif
```

The prefix is taken off the front, so the entity name is whatever is left and may have
underscores in it: `s0011_rib_left_11_mask.tif` is the entity `rib_left_11`.

**Published data is read where it lies.** A segmentation you downloaded is not going to be
renamed to suit a reader, so two other layouts work as they come:

- **Other formats.** NIfTI (`.nii`, `.nii.gz`), NRRD and MetaImage are read alongside TIFF,
  through SimpleITK. Their headers carry a reliable voxel size, which TIFF often does not -
  spacing is taken as millimetres and converted, the convention every reader of these files
  uses. `--voxel-size-um` overrides it.
- **Entities in a subfolder**, named by nothing but the structure - a `segmentations/`,
  `masks/` or `labels/` folder beside the source image. There is no prefix to strip, and
  whether each file is a label or a mask is read off its content rather than guessed from
  its name: more than one distinct non-zero value is a label volume, one is a mask.
  `dry-run` lists these as `auto` and says so.

```text
s0011/                      # TotalSegmentator, exactly as downloaded
  ct.nii.gz
  segmentations/
    liver.nii.gz
    rib_left_11.nii.gz
    ...                     # 117 of them
```

- **A remote store, read as a crop.** A folder holding one `source.json` and no images is
  an object too: the manifest names a chunked store (N5 or Zarr, local or on S3), the arrays
  in it, and the window to read. A chunked array is fetched block by block, so a 512³ crop of
  OpenOrganelle's 122-gigavoxel HeLa cell is 0.64% of it, in under five seconds - full 4 nm
  resolution without downloading the cell. Needs the `remote` extra
  (`pip install 'label-anatomy[remote]'`).

```json
{
  "store": "s3://janelia-cosem-datasets/jrc_hela-2/jrc_hela-2.n5",
  "scale": "s0",
  "crop": "3712:4224,256:768,5760:6272",
  "source": "em/fibsem-uint16",
  "entities": { "mito": "labels/mito_seg", "er": "labels/er_seg" }
}
```

An object carrying that many structures is 118 channels of the whole field of view, so
`--entities liver,spleen,aorta` is what makes it measurable at all. A single volume whose
ids each mean a different structure is split by `--label-map`, which takes a JSON file of
`{"1": "liver", "2": "spleen"}` and makes an entity per id it names.

One of the masks bounds the object, and `--object-mask NAME` says which. Always, with no
name-based guessing, because every distance and polarity in the report is measured against
it. Naming a mask a folder does not have is an error rather than a silent fallback, and the
message lists what that folder does have. An object folder can sit on its own, in a flat batch
(`cells/cell_a/`, `cells/cell_b/`), or in a grouped batch (`experiment/control/cell_a/`,
`experiment/treated/cell_b/`), where the group folder is what `-p` imports.

## Output

```text
report.parquet                            # rows per object, structure, instance and contact
report_meshes/<object>/geometry.parquet   # only with --with-mesh
```

Everything measured lands in `report.parquet`, as rows at four depths: one per object
(`obs_level = 0`), one per structure (`obs_level = 1`) with volume, surface area, sphericity
and instance counts, and one each per instance, per instance-to-structure distance and per
contact below them, told apart by `row_type`. See the
[reference](REFERENCE.md#data-model) for the full model.

Geometry never enters the report, because meshes would multiply the size of a table every
query loads. It goes beside it, one `geometry.parquet` per object holding a mesh (or, for a
2D object, an outline) per instance, a skeleton for each structure `--skeleton-entities`
named, and the touching pairs - a geometry file stands on its own, for Blender and for a
hosted copy of it. The object row records
where, in `mesh_geometry_file`, which is how the 3D sections find it.

## Blender

Use `geometry_to_blender.py` to import an object's `geometry.parquet` into Blender.

Requirements:

- Blender with `pandas` and `pyarrow` available in Blender's Python
  (`<blender>/python/bin/python3 -m pip install pandas pyarrow`)
- `geometry.parquet` generated by `label-anatomy process --with-mesh` or
  `label-anatomy mesh`, from **3D** objects. A 2D object carries outlines rather
  than meshes, which the report's gallery draws and Blender has no use for

Run headless:

```bash
blender --background --python geometry_to_blender.py -- /path/to/geometry.parquet [out.blend] [out_render.png]
```

Or run inside Blender Script Editor:

1. Open `geometry_to_blender.py`.
2. Set `GEOMETRY_PATH` (optionally `OUT_BLEND`, `OUT_RENDER`).
3. Run script (`Alt+P`).
