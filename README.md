# Organella: measuring label relationships in 3D

<img src="https://raw.githubusercontent.com/ida-mdc/organella/main/docs/organella.png" alt="A figure drawn entirely out of organelles, holding a measuring tape" align="right" width="190">

Organella measures segmented objects - 2D or 3D, one or a batch of them - and produces a
single report including distributions, distances, contacts, and 3D visualizations. 
This project is targeting, but not limited to the study of 3D structures in single cells.

### Online viewer:  https://ida-mdc.github.io/organella/

The online viewer can be used to visualize local report files. It does not upload any data to a remote location, it runs locally in the browser.

### Example report: **[seven mouse β cells, from Müller et al.](https://ida-mdc.github.io/organella/?data=https%3A%2F%2Fdcache-doma-door01.desy.de%2FHelmholtz%2FHIP%2Fcollaborations%2FOrganella%2Freports%2Fmueller-betacells.parquet)**

FIB-SEM, seven cells grouped in two conditions (stimulated with low and high glucose) and
segmented into plasma membrane, nucleus, Golgi apparatus, mitochondria, insulin secretory
granules, microtubules, centrioles and axoneme.

<br clear="right">

## Screenshots from the reports

|                                                                                                                                                                                                                                                                                                                           |                                                                                                                                                                                                                                                                                                                                                                                                        |
|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [![The 3D view of a cell drawn from its geometry](https://raw.githubusercontent.com/ida-mdc/organella/main/docs/report-3d.png)](https://raw.githubusercontent.com/ida-mdc/organella/main/docs/report-3d.png)<br>**The object in 3D,** if processing was executed with `--with-mesh`.                                      | [![Composition per object and boxes per group](https://raw.githubusercontent.com/ida-mdc/organella/main/docs/report-distributions.png)](https://raw.githubusercontent.com/ida-mdc/organella/main/docs/report-distributions.png)<br>**Composition, and groups compared.** A systematic overview to assess missing labels and detailled plots for overall and individual shape and relationship metrics. |
| [![The instance gallery](https://raw.githubusercontent.com/ida-mdc/organella/main/docs/report-instances.png)](https://raw.githubusercontent.com/ida-mdc/organella/main/docs/report-instances.png)<br>**A gallery visualizing indivual label instances, sortable by any metric that was calculated. Inspectable skeletons. | [![Voxel-distance histograms](https://raw.githubusercontent.com/ida-mdc/organella/main/docs/report-voxel-distances.png)](https://raw.githubusercontent.com/ida-mdc/organella/main/docs/report-voxel-distances.png)<br>**A structure's voxels binned by how far each one is from another structure.                   |

## Workflow

1. Organise your images in one of the [input layouts](#your-input).
2. `organella dry-run` to check what will be analysed.
3. `organella process` to write `report.parquet` and, with `--with-mesh`, the geometry.
4. `organella view` to open the report page in the browser.
5. Optionally import an object's geometry into [Blender](#blender).

## Try it

Install Organella:

```bash
pip install organella
```

Or with [uv](https://docs.astral.sh/uv/getting-started/installation/), which is quicker:
`uv pip install organella`.

Then test, measure, and view your label dataset:

```bash
organella dry-run experiment/
organella process experiment/ -o report.parquet --with-mesh
organella view report.parquet
```

Most important processing arguments:

| |                                                                                                                                    |
| --- |------------------------------------------------------------------------------------------------------------------------------------|
| `--object-mask pm` | The mask that bounds each object. It decides the origin of every distance and polarity, and everything inside it is clipped to it. |
| `-p control -p treated` | Subdirectories to import as groups. That grouping becomes the default comparison in every chart.                                   |
| `--voxel-size-um 0.1,0.02,0.02` | The resolution of the image / volume. `z,y,x` for a volume,  `y,x` for a 2D image.                                                 |
| `--skeletons mito,ER` | Calculates and measures the skeletons for these structures.                                                                        |
| `--entities mito,ER` | Limit measures to these structures.                                                                                                |
| `--with-mesh` | Calculate the geometry of each structure to visualize it in 3D, exported to `<output>_meshes/`.                                    |

Every argument is listed under [Parameters of `process`](#parameters-of-process).

**Check the input first.** `dry-run` reads image headers only - no analysis, no output - 
and prints per object the label and mask entities
found (`*` marks the object mask), which entities are
missing in which objects, and a suggested `--max-workers`. Exit code is `1` if any object
cannot be analysed. Example output:

```text
control/cell_b
  labels  mito
  masks   nucleus, pm*
  warn    ignored readme_overlay.tif: not <prefix>_<name>_label|labels|mask

===== 3 object folder(s) =====  (* = object mask)
  label:mito               2/3   ← missing in some objects
  mask:pm                  3/3
```

## Reading the report

`organella view report.parquet` serves the report, its geometry and the page locally and opens it. 

Alternatively, open **<https://ida-mdc.github.io/organella/>** and drop
`report.parquet` on it: the page parses the parquet in the browser, so nothing is uploaded and
no server runs. **The 3D sections need the geometry too**. Press **Add geometry** and pick either the `report_meshes` folder or the
`geometry.parquet` files themselves. The `organelle view` command attaches them for you and you don't need to do anything else there.

In the page, **Charts** switches every panel between boxes and histograms, and
**Significance** adds Mann-Whitney brackets to the facets. To calculate the p-value, only one averaged value per cell is taken into account to preserve the comparison of independent measurements.  

**Every distance panel is drawn against chance.** Relationship measurements in bounded regions like cells
depend heavily on the shape of the boundary. Therefore, any distance plot includes a dotted line representing how close
all pixels in the object boundary are to the specific structure. `--baseline-exclude` decides
which label should be left out of that region (i.e. nucleus).

**Polarity is relative to a structure.** Every polarity column is
measured from the object mask's own centroid, which is the right origin but points wherever
the volume happened to be oriented - so an azimuth means nothing from one object to the next.
Pick a structure in *Polarity* and the direction from the centre to
its centre becomes 0°. The section reports the angle per instance, the two collective measures per
object - **R**, how tightly a structure's directions agree, and **V**, the same strength signed
by whether it leans towards the reference (+1) or away from it (-1) - the angle against the
distance from the centre, and one circular map per object. R and V are
[Polarity-JaM](https://www.polarityjam.com)'s polarity indices ([Giese et al., *Nat Commun*
2025](https://doi.org/10.1038/s41467-025-56643-x)). The circular maps are one per
object on purpose: the axis fixes 0°, but nothing in the data fixes the rotation *about* that
axis, so which side of the circle a structure falls on is arbitrary.

## Your input

Entity files must match `<prefix>_<name>_label.tif` (or `_labels.tif`) and
`<prefix>_<name>_mask.tif`, where `<prefix>` is the source image basename. The prefix is taken
off the front, i.e. for `s0011_rib_left_11_mask.tif`, `s0011` is the prefix and `rib_left_11` is the entity.

```text
my_cell/
  sample.tif
  sample_mito_label.tif
  sample_nucleus_mask.tif
  sample_membrane_mask.tif
```

An object folder can sit on its own, in a flat batch (`cells/cell_a/`, `cells/cell_b/`), or in
a grouped batch (`experiment/control/cell_a/`, `experiment/treated/cell_b/`), where the group
folder is what `-p` imports.

**Other formats.** NIfTI (`.nii`, `.nii.gz`), NRRD and MetaImage are read through SimpleITK.
Their headers carry a reliable voxel size, which TIFF often does not; spacing is read as
millimetres, the convention every reader of these files uses.
 
**Entities in a subfolder** named by nothing but the structure - a `segmentations/`, `masks/`
or `labels/` folder beside the source image. There is no prefix to strip, and label-or-mask
is read off the folder name instead of the file name.
 
**A remote store, read as a crop.** A folder holding one `source.json` and no images names a
chunked store (N5 or Zarr, local or on S3), the arrays in it, and the window to read. A 512³
crop of OpenOrganelle's 122-gigavoxel HeLa cell is 0.64% of it, in under five seconds, at
full 4 nm resolution. Needs the `remote` extra: `pip install 'organella[remote]'`.

```json
{
  "store": "s3://janelia-cosem-datasets/jrc_hela-2/jrc_hela-2.n5",
  "scale": "s0",
  "crop": "3712:4224,256:768,5760:6272",
  "source": "em/fibsem-uint16",
  "entities": { "mito": "labels/mito_seg", "er": "labels/er_seg" }
}
```

## Output

```text
report.parquet                            # rows per object, structure, instance and contact
report_meshes/<object>/geometry.parquet   # only with --with-mesh
```

Everything measured lands in `report.parquet`, as rows at four depths told apart by `row_type`:

| `row_type` | `obs_level` | one row per |
| --- | --- | --- |
| `object` | 0 | object folder: extent, provenance, and the whole-object totals |
| `entity` | 1 | structure, by name: volume, surface area, sphericity, instance counts |
| `instance` | 2 | labelled instance: its own size, shape, polarity and nearest neighbour |
| `distance` | 2 | instance × target structure: how far that instance is from it |
| `contact` | 2 | touching pair of instances of one structure, with their gap |

Geometry goes beside the report. One `geometry.parquet` per object holds a surface per instance (an
outline, for a 2D object), the skeletons, and the touching pairs.

## Commands

| |                                                                                       |
| --- |---------------------------------------------------------------------------------------|
| `organella dry-run DIR` | What would be analysed, reading image headers only.                                   |
| `organella process DIR -o REPORT` | Analyse a batch, write the report.                                                    |
| `organella mesh DIR -o OUTDIR` | Process geometry only, for a report you already have.                                 |
| `organella view REPORT` | Serve the report, its geometry and the page, and open it.                             |
| `organella page` | Print the path of the standalone page.                                                |
| `organella colours REPORT PALETTE` | Recolour a report.                                                                    |
| `organella describe REPORT TEXT` | Add a description to an existing report. This will be shown at the top of the report. |


## Parameters of `process`

**Input**

| |                                                                                                                                                 |
| --- |-------------------------------------------------------------------------------------------------------------------------------------------------|
| `-o, --output FILE` | Where to write the report. Required.                                                                                                            |
| `-p, --paths TEXT` | Subdirectory to import as its own group, repeatable. Becomes the default grouping in every chart.                                               |
| `--object-mask NAME` | The mask that bounds each object. Other labels and masks are clipped to stay within this mask. It decides the origin of polarity.               |
| `--description TEXT` | What this data is and who it credits. The report page shows it above the first section.                                                         |
| `--object-noun WORD` | What one measured thing is called in the report, e.g. `cell`, or `nucleus/nuclei` for an irregular plural. Presentation only.                   |
| `--voxel-size-um Z,Y,X` | Voxel size in µm; `y,x` for a plane. Inferred from the source metadata when omitted.                                                            |
| `--entities NAMES` | Measure only these, plus the object mask.                                                                                                       |
| `--label-map FILE` | JSON of `{"1": "liver"}`, splitting one volume whose ids each mean a different structure into an entity per id. Only named ids become entities. |
| `--label-map-entity NAME` | Which entity `--label-map` splits. Needed only when a folder has more than one label entity.                                                    |
| `--auto-label-masks` | Automatically convert masks (binary datasets) with several connected components to label entities.                                             |

**What gets measured**

| |                                                                                                                                                                                                                                      |
| --- |--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `--no-clip` | Measure outside the object mask too. Clipped to it by default.                                                                                                                                                                       |
| `--no-instances` | Entity-level morphology only: no per-instance rows.                                                                                                                                                                                  |
| `--no-contacts` | Skip the calculation of contacts between entities.                                                                                                                                                                                   |
| `--contact-max-um T` | Largest gap between two instances of one structure that still counts as a contact. Default 0.5                                                                                                                                       |
| `--skeletons NAMES` | Structures to skeletonise, for branches, length and tortuosity. Opt-in: it is the most expensive thing in a run.                                                                                                                     |
| `--max-skeleton-voxels N` | skip skeletons for instances above this voxel count. Default 500000                                                                                                                                                                  |
| `--polarity-spread` | Measure each instance's angular spread on the polarity sphere.                                                                                                                                                                       |
| `--distance-histograms` | Measure per-instance distance distributions, not just the minimum.                                                                                                                                                                   |
| `--baseline-exclude NAMES` | Structures to leave out of the region every distance is read against. Each structure gets a chance distribution - its distance from everywhere in the object. Name the structures an instance could never sit inside, e.g. `nucleus. |
| `--colours FILE` | also `--colors`. JSON of `{"mito": "#d62728"}`. Lands in the report, so every chart draws that structure the same. Unnamed structures keep the built-in palette.                                                                     |

**Geometry**, all of it only with `--with-mesh`:

| |                                                                                                                                                                                                                                                                         |
| --- |-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `--with-mesh` | Write per-object geometry for the 3D views and Blender. Goes to `<output>_meshes/`.                                                                                                                                                                                     |
| `--geometry-as NAME=KIND,...` | How a structure's surface is stored: `mesh`, `ellipsoid` or `tube`, e.g. `vesicle=ellipsoid`. Decided from the measured shape when unnamed - a round instance becomes a 60-byte ellipsoid. No measurement changes; the surface is only for visualization in the report. |
| `--mesh-max-vertices N` | Max number of vertices per surface; 0 lifts the cap. Default 200000.                                                                                                                                                                                                    |
| `--mesh-smooth-sigma SIGMA` | Gaussian sigma before marching cubes. Default 0.7; 0 disables smoothing.                                                                                                                                                                                                |
| `--mesh-step-size N` | Marching-cubes step size; 1 is full resolution. Default 2.                                                                                                                                                                                                              |
| `--mesh-target-reduction F` | Decimation fraction. Default 0.8, keeping ~20% of faces.                                                                                                                                                                                                                |
| `--mesh-level L` | Iso-surface level on the signed distance field. Default 0.                                                                                                                                                                                                              |
| `--mesh-surface-method` | `marching-cubes` or `surface-nets`. The dual method has less staircase noise, no fewer vertices, and is slower.                                                                                                                                                         |
| `--mesh-workers N` | Processes used for meshing. Default: the cores the object pool is not using.                                                                                                                                                                                            |
| `--reuse-geometry` | Keep any `geometry.parquet` an object already has.                                                                                                                                                |

**The run**

| |                                                                                                                                                         |
| --- |---------------------------------------------------------------------------------------------------------------------------------------------------------|
| `--max-workers N` | Worker processes. Default: worked out from memory.                                                                                                      |
| `--num-threads N` | Kimimaro worker count. Default 1, because objects already run in parallel.                                                                              |
| `--resume` | Skip objects an interrupted run already measured. Each object's rows go to `<output>_parts/` as it finishes and are removed once the report is written. |

## Sharing a report

The geometry stays a folder of one file per object, so it travels with
the report in one of two shapes:

- Share `report.parquet` and its `report_meshes/` folder, and the reader runs
  `organella view report.parquet`.
- Upload the two together, unchanged, and open the page with `?data=<url of the parquet>`.
  The geometry is located next to the parquet as `<name>_meshes/`, so there is nothing else
  to pass.


## Development

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e '.[test]'      # the suite needs pytest and duckdb
.venv/bin/python -m pytest
```

The report page is one file with no build step, edit
[`src/organella/report/organella_report.html`](src/organella/report/organella_report.html)
and reload the browser. 


## Blender

`geometry_to_blender.py` imports an object's `geometry.parquet`. It needs Blender with `pandas`
and `pyarrow` in Blender's own Python
(`<blender>/python/bin/python3 -m pip install pandas pyarrow`), and geometry from a **3D**
object - a 2D object carries outlines, which the report's gallery draws and Blender has no use
for.

```bash
blender --background --python geometry_to_blender.py -- geometry.parquet [out.blend] [out.png]
```

Or open the script in Blender's Script Editor, set `GEOMETRY_PATH` (optionally `OUT_BLEND`,
`OUT_RENDER`) and run it with `Alt+P`.


## History

Organella is the successor of [CellSketch](https://github.com/betaseg/cellsketch), which was published in these articles:
- [Mueller et al. 2021](https://rupress.org/jcb/article/220/2/e202010039/211599/3D-FIB-SEM-reconstruction-of-microtubule-organelle): 3D FIB-SEM reconstruction of microtubule–organelle interaction in whole primary mouse β cells, Journal of Cell Biology 220 (2), e202010039
- [Mueller, Schmidt et al. 2024](https://www.nature.com/articles/s41596-024-00957-5): Modular segmentation, spatial analysis and visualization of volume electron microscopy datasets, Nature Protocols 19 (5), 1436-1466