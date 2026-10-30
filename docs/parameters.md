# Parameters of `organella process`

`organella process --help` lists the same options.

## Input

|  |  |
| --- | --- |
| `-o, --output FILE` | Where to write the report. Required. |
| `-p, --paths TEXT` | Subdirectory to import as its own group, repeatable. Becomes the default grouping in every chart. |
| `--object-mask NAME` | The mask that bounds each object. Other labels and masks are clipped to stay within this mask. It decides the origin of polarity. |
| `--description TEXT` | What this data is and who it credits. The report page shows it above the first section. |
| `--object-noun WORD` | What one measured thing is called in the report, e.g. `cell`, or `nucleus/nuclei` for an irregular plural. Presentation only. |
| `--voxel-size-um Z,Y,X` | Voxel size in µm; `y,x` for a plane. Inferred from the source metadata when omitted. |
| `--entities NAMES` | Measure only these, plus the object mask. |
| `--label-map FILE` | JSON of `{"1": "liver"}`, splitting one volume whose ids each mean a different structure into an entity per id. Only named ids become entities. |
| `--label-map-entity NAME` | Which entity `--label-map` splits. Needed only when a folder has more than one label entity. |

## What gets measured

|  |  |
| --- | --- |
| `--no-clip` | Measure outside the object mask too. Clipped to it by default. |
| `--no-instances` | Entity-level morphology only: no per-instance rows. |
| `--no-contacts` | Skip the calculation of contacts between entities. |
| `--contact-max-um T` | Largest gap between two instances of one structure that still counts as a contact. Default 0.5 |
| `--skeletons NAMES` | Structures to skeletonise, for branches, length and tortuosity. Opt-in: it is the most expensive thing in a run. |
| `--max-skeleton-voxels N` | skip skeletons for instances above this voxel count. Default 500000 |
| `--skeleton-scale R` | reach around a traced path, in local radii. Default 3 |
| `--skeleton-const-voxels N` | extra reach, in voxels of the coarsest axis. Default 6 |
| `--skeleton-prune R` | cut terminal branches shorter than R radii; 0 keeps all. Default 2 |
| `--polarity-spread` | Measure each instance's angular spread on the polarity sphere. |
| `--distance-histograms` | Measure per-instance distance distributions, not just the minimum. |
| `--baseline-exclude NAMES` | Structures left out of the region every distance is read against, e.g. `nucleus`: ones an instance can never sit inside. |
| `--colours FILE` | also `--colors`. JSON of `{"mito": "#d62728"}`. Lands in the report, so every chart draws that structure the same. Unnamed structures keep the built-in palette. |

## Geometry

All of it only with `--with-mesh`.

|  |  |
| --- | --- |
| `--with-mesh` | Write per-object geometry for the 3D views and Blender. Goes to `<output>_meshes/`, or to `--mesh-dir DIR`. |
| `--geometry-as NAME=KIND,...` | How a surface is stored: `mesh`, `ellipsoid` or `tube`, e.g. `vesicle=ellipsoid`; `+skeleton` also skeletonises. Unnamed: decided from the shape. Measurements do not change. |
| `--mesh-dir DIR` | Where the geometry goes. Default `<output>_meshes`. |
| `--mesh-max-vertices N` | Max number of vertices per surface. Default 0: no cap. |
| `--mesh-smooth-sigma SIGMA` | Gaussian sigma before marching cubes. Default 0.7; 0 disables smoothing. |
| `--mesh-step-size N` | Marching-cubes step size; 1 is full resolution. Default 1. |
| `--mesh-target-reduction F` | Decimation fraction. Default 0.8, keeping ~20% of faces. |
| `--mesh-level L` | Iso-surface level on the signed distance field. Default 0. |
| `--reuse-geometry` | Keep any `geometry.parquet` an object already has. |

## Volumes too big to hold

 Zarr and N5 stores are read a chunk at a time, so memory follows the chunk and not the volume.

|  |  |
| --- | --- |
| `--chunk-edge N` | Voxels along an edge of the chunks the volume is read in. Must exceed the reach in voxels; memory per thread grows with its cube. Default 128. |
| `--reach-um D` | How far a distance to another structure is exact, in µm. Default 1. Past it a distance reads as the reach. |
| `--cache-gb G` | Memory for decoded chunks of a store. |

## The run

|  |  |
| --- | --- |
| `--threads N` | Most threads the run uses. Default: one per core. |
| `--resume` | Skip objects an interrupted run already measured. Each object's rows go to `<output>_parts/` as it finishes and are removed once the report is written. |

