# Organella (next)

Measures segmented objects - one or a batch - and writes one parquet report that the Organella page reads.
Chunked and lazy: memory does not grow with the size of the object.

> A rewrite in progress. It reads the same layouts and writes the same column names as Organella, for the parts
> below; the rest is [not ported yet](#not-ported-yet). Developers: [DEVELOPING.md](DEVELOPING.md).

## Try it

```bash
organella process experiment/ -o report.parquet --object-mask cell
organella view report.parquet
```

| `process` option | |
| --- | --- |
| `-o, --output FILE` | Where the report goes. Required. |
| `-p, --paths NAME` | Subfolder to import as a group, repeatable. |
| `--object-mask NAME` | The mask that bounds each object; everything else is clipped to it. |
| `--voxel-size-um Z,Y,X` | Voxel size in µm. Read from the files when omitted. |
| `--reach-um D` | Distances are exact up to D µm (default 1); farther ones read as D and are flagged. |

## Your input

- **Folders of TIFFs:** `<prefix>_<name>_label.tif` (instances) and `<prefix>_<name>_mask.tif` (one region) per object
  folder, beside the source image `<prefix>.tif`. A batch is a folder of object folders, optionally in groups.
- **A Zarr or N5 store**, local or `s3://`: pass a manifest, `organella process cell.json -o report.parquet`.

```json
{
  "store": "s3://janelia-cosem-datasets/jrc_hela-2/jrc_hela-2.n5",
  "voxel_size_um": [0.008, 0.008, 0.008],
  "crop": "3712:4224,256:768,5760:6272",
  "entities": { "cell": "labels/pm_filled", "mito": "labels/mito_seg" },
  "masks": ["cell"]
}
```

## Output

One `report.parquet`, rows by `row_type`:

| `row_type` | one row per |
| --- | --- |
| `object` | object: extent, voxel size, group, centre |
| `entity` | structure: total volume, instance count |
| `instance` | labelled instance: volume, centroid |
| `distance` | instance and target structure: nearest distance |
| `baseline` | target structure: distance from every voxel of the object, as a histogram |

## Not ported yet

Surface area and sphericity, skeletons, meshes and the 3D geometry files, polarity, contacts, `--baseline-exclude`,
`cells`, label maps, NIfTI, `dry-run`, `--resume`. The old package has them.
