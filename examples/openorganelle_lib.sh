# Shared by openorganelle_slurm.sh and openorganelle_cells_slurm.sh: what is measured in each OpenOrganelle volume, and how its
# cells are cut apart. Sourced from the repository root; sets scale, mask_scale and extra (more `organella process` options).

openorganelle_settings() {   # DATASET
    bucket=s3://janelia-cosem-datasets
    case "$1" in
    jrc_hela-2|jrc_hela-3)
        scale=${SCALE:-s2}
        mask_scale=${MASK_SCALE:-s3}
        extra=(--skeletons mito)
        ;;
    jrc_mus-liver-6|jrc_mus-liver-7)
        scale=${SCALE:-s2}
        mask_scale=${MASK_SCALE:-s4}
        extra=()
        ;;
    *)
        echo "unknown dataset $1 (jrc_hela-2, jrc_hela-3, jrc_mus-liver-6, jrc_mus-liver-7)" >&2
        return 2
        ;;
    esac
}

openorganelle_cut() {   # DATASET OUT: <OUT>/<dataset>/cells, a manifest and a small mask per cell
    local dataset=$1 out=$2
    openorganelle_settings "$dataset" || return $?
    case "$dataset" in
    jrc_hela-2|jrc_hela-3)
        # The cells are derived: the membrane and the space around bound a cell, the nucleus seeds it, a watershed cuts
        # touching ones apart. The label arrays are instance maps, except the nucleus' own, which holds one id: it is a mask,
        # a whole structure. The seed size is 2 um3: at the default of 40 the fragments of a neighbouring cell's nucleus are
        # merged into the cell beside it.
        uv run organella cells "$bucket/$dataset/$dataset.n5" "$out/$dataset/cells" --prefix "${dataset#jrc_}" \
            --source em/fibsem-uint16 --membrane labels/pm_seg --outside labels/ecs_seg --seed labels/nucleus_seg \
            --scale "$scale" --mask-scale "$mask_scale" --min-seed-um3 2 \
            --entity mito=labels/mito_seg --entity mito-mem=labels/mito-mem_seg --entity er=labels/er_seg \
            --entity golgi=labels/golgi_seg --entity lyso=labels/lyso_seg --entity ld=labels/ld_seg \
            --entity endo=labels/endo_seg --entity vesicle=labels/vesicle_seg --entity nucleus=labels/nucleus_seg \
            --instances mito,mito-mem,er,golgi,lyso,ld,endo,vesicle --masks nucleus
        ;;
    jrc_mus-liver-6|jrc_mus-liver-7)
        # The volume ships a segmentation of its cells, so they are cut from that, not derived. Its structure arrays are
        # binary, so liver_cells.py declares every structure a mask.
        uv run python examples/liver_cells.py "$dataset" "$out/$dataset/cells" \
            --scale "$scale" --mask-scale "$mask_scale" --min-um3 "${MIN_UM3:-300}"
        ;;
    esac
}
