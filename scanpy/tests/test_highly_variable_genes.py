from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import sparse

import scanpy as sc
from scanpy.testing._helpers import _check_check_values_warnings
from scanpy.testing._helpers.data import pbmc3k, pbmc68k_reduced
from scanpy.testing._pytest.marks import needs

FILE = Path(__file__).parent / Path("_scripts/seurat_hvg.csv")
FILE_V3 = Path(__file__).parent / Path("_scripts/seurat_hvg_v3.csv.gz")
FILE_V3_BATCH = Path(__file__).parent / Path("_scripts/seurat_hvg_v3_batch.csv")


def test_highly_variable_genes_runs():
    adata = sc.datasets.blobs()
    sc.pp.highly_variable_genes(adata)


def test_highly_variable_genes_supports_batch():
    adata = sc.datasets.blobs()
    gen = np.random.default_rng(0)
    adata.obs["batch"] = pd.array(
        gen.binomial(3, 0.5, size=adata.n_obs), dtype="category"
    )
    sc.pp.highly_variable_genes(adata, batch_key="batch")
    assert "highly_variable_nbatches" in adata.var.columns
    assert "highly_variable_intersection" in adata.var.columns


def test_highly_variable_genes_supports_layers():
    adata = sc.datasets.blobs()
    gen = np.random.default_rng(0)
    adata.obs["batch"] = pd.array(
        gen.binomial(4, 0.5, size=adata.n_obs), dtype="category"
    )
    sc.pp.highly_variable_genes(adata, batch_key="batch", n_top_genes=3)
    assert "highly_variable_nbatches" in adata.var.columns
    assert adata.var["highly_variable"].sum() == 3
    highly_var_first_layer = adata.var["highly_variable"]

    adata = sc.datasets.blobs()
    assert isinstance(adata.X, np.ndarray)
    new_layer = adata.X.copy()
    gen.shuffle(new_layer)
    adata.layers["test_layer"] = new_layer
    adata.obs["batch"] = gen.binomial(4, 0.5, size=(adata.n_obs))
    adata.obs["batch"] = adata.obs["batch"].astype("category")
    sc.pp.highly_variable_genes(
        adata, batch_key="batch", n_top_genes=3, layer="test_layer"
    )
    assert "highly_variable_nbatches" in adata.var.columns
    assert adata.var["highly_variable"].sum() == 3
    assert (highly_var_first_layer != adata.var["highly_variable"]).any()


def test_highly_variable_genes_no_batch_matches_batch():
    adata = sc.datasets.blobs()
    sc.pp.highly_variable_genes(adata)
    no_batch_hvg = adata.var["highly_variable"].copy()
    assert no_batch_hvg.any()
    adata.obs["batch"] = "batch"
    adata.obs["batch"] = adata.obs["batch"].astype("category")
    sc.pp.highly_variable_genes(adata, batch_key="batch")
    assert np.all(no_batch_hvg == adata.var["highly_variable"])
    assert np.all(
        adata.var["highly_variable_intersection"] == adata.var["highly_variable"]
    )


def test_highly_variable_genes_():
    adata = sc.datasets.blobs()
    adata.obs["batch"] = np.tile(["a", "b"], adata.shape[0] // 2)
    sc.pp.highly_variable_genes(adata, batch_key="batch")
    assert adata.var["highly_variable"].any()

    colnames = [
        "means",
        "dispersions",
        "dispersions_norm",
        "highly_variable_nbatches",
        "highly_variable_intersection",
        "highly_variable",
    ]
    hvg_df = sc.pp.highly_variable_genes(adata, batch_key="batch", inplace=False)
    assert hvg_df is not None
    assert np.all(np.isin(colnames, hvg_df.columns))


@pytest.mark.parametrize("base", [None, 10])
@pytest.mark.parametrize("flavor", ["seurat", "cell_ranger"])
def test_highly_variable_genes_keep_layer(base, flavor):
    adata = pbmc3k()
    # cell_ranger flavor can raise error if many 0 genes
    sc.pp.filter_genes(adata, min_counts=1)

    sc.pp.log1p(adata, base=base)
    assert isinstance(adata.X, sparse.csr_matrix)
    X_orig = adata.X.copy()

    if flavor == "seurat":
        sc.pp.highly_variable_genes(adata, n_top_genes=50, flavor=flavor)
    elif flavor == "cell_ranger":
        sc.pp.highly_variable_genes(adata, flavor=flavor)
    else:
        assert False

    assert np.allclose(X_orig.A, adata.X.A)


def _check_pearson_hvg_columns(output_df: pd.DataFrame, n_top_genes: int):
    assert pd.api.types.is_float_dtype(output_df["residual_variances"].dtype)

    assert output_df["highly_variable"].to_numpy().dtype is np.dtype("bool")
    assert np.sum(output_df["highly_variable"]) == n_top_genes

    assert np.nanmax(output_df["highly_variable_rank"].to_numpy()) <= n_top_genes - 1


def test_highly_variable_genes_pearson_residuals_inputchecks(pbmc3k_parametrized_small):
    adata = pbmc3k_parametrized_small()

    # depending on check_values, warnings should be raised for non-integer data
    if adata.X.dtype == "float32":
        adata_noninteger = adata.copy()
        x, y = np.nonzero(adata_noninteger.X)
        adata_noninteger.X[x[0], y[0]] = 0.5

        _check_check_values_warnings(
            function=sc.experimental.pp.highly_variable_genes,
            adata=adata_noninteger,
            expected_warning="`flavor='pearson_residuals'` expects raw count data, but non-integers were found.",
            kwargs=dict(
                flavor="pearson_residuals",
                n_top_genes=100,
            ),
        )

    # errors should be raised for invalid theta values
    for theta in [0, -1]:
        with pytest.raises(ValueError, match="Pearson residuals require theta > 0"):
            sc.experimental.pp.highly_variable_genes(
                adata.copy(), theta=theta, flavor="pearson_residuals", n_top_genes=100
            )

    with pytest.raises(
        ValueError, match="Pearson residuals require `clip>=0` or `clip=None`."
    ):
        sc.experimental.pp.highly_variable_genes(
            adata.copy(), clip=-1, flavor="pearson_residuals", n_top_genes=100
        )


@pytest.mark.parametrize("subset", [True, False], ids=["subset", "full"])
@pytest.mark.parametrize(
    "clip", [None, np.Inf, 30], ids=["noclip", "infclip", "30clip"]
)
@pytest.mark.parametrize("theta", [100, np.Inf], ids=["100theta", "inftheta"])
@pytest.mark.parametrize("n_top_genes", [100, 200], ids=["100n", "200n"])
def test_highly_variable_genes_pearson_residuals_general(
    pbmc3k_parametrized_small, subset, clip, theta, n_top_genes
):
    adata = pbmc3k_parametrized_small()
    # cleanup var
    del adata.var

    # compute reference output
    residuals_res = sc.experimental.pp.normalize_pearson_residuals(
        adata, clip=clip, theta=theta, inplace=False
    )
    assert isinstance(residuals_res, dict)
    residual_variances_reference = np.var(residuals_res["X"], axis=0)

    if subset:
        # lazyly sort by residual variance and take top N
        top_n_idx = np.argsort(-residual_variances_reference)[:n_top_genes]
        # (results in sorted "gene order" in reference)
        residual_variances_reference = residual_variances_reference[top_n_idx]

    # compute output to be tested
    output_df = sc.experimental.pp.highly_variable_genes(
        adata,
        flavor="pearson_residuals",
        n_top_genes=n_top_genes,
        subset=subset,
        inplace=False,
        clip=clip,
        theta=theta,
    )
    assert output_df is not None

    sc.experimental.pp.highly_variable_genes(
        adata,
        flavor="pearson_residuals",
        n_top_genes=n_top_genes,
        subset=subset,
        inplace=True,
        clip=clip,
        theta=theta,
    )

    # compare inplace=True and inplace=False output
    pd.testing.assert_frame_equal(output_df, adata.var)

    # check output is complete
    for key in [
        "highly_variable",
        "means",
        "variances",
        "residual_variances",
        "highly_variable_rank",
    ]:
        assert key in output_df.keys()

    # check consistency with normalization method
    if subset:
        # sort values before comparing as reference is sorted as well for subset case
        sort_output_idx = np.argsort(-output_df["residual_variances"].to_numpy())
        assert np.allclose(
            output_df["residual_variances"].to_numpy()[sort_output_idx],
            residual_variances_reference,
        )
    else:
        assert np.allclose(
            output_df["residual_variances"].to_numpy(), residual_variances_reference
        )

    # check hvg flag
    hvg_idx = np.where(output_df["highly_variable"])[0]
    topn_idx = np.sort(
        np.argsort(-output_df["residual_variances"].to_numpy())[:n_top_genes]
    )
    assert np.all(hvg_idx == topn_idx)

    # check ranks
    assert np.nanmin(output_df["highly_variable_rank"].to_numpy()) == 0

    # more general checks on ranks, hvg flag and residual variance
    _check_pearson_hvg_columns(output_df, n_top_genes)


@pytest.mark.parametrize("subset", [True, False], ids=["subset", "full"])
@pytest.mark.parametrize("n_top_genes", [100, 200], ids=["100n", "200n"])
def test_highly_variable_genes_pearson_residuals_batch(
    pbmc3k_parametrized_small, subset, n_top_genes
):
    adata = pbmc3k_parametrized_small()
    # cleanup var
    del adata.var
    n_genes = adata.shape[1]

    output_df = sc.experimental.pp.highly_variable_genes(
        adata,
        flavor="pearson_residuals",
        n_top_genes=n_top_genes,
        batch_key="batch",
        subset=subset,
        inplace=False,
    )
    assert output_df is not None

    sc.experimental.pp.highly_variable_genes(
        adata,
        flavor="pearson_residuals",
        n_top_genes=n_top_genes,
        batch_key="batch",
        subset=subset,
        inplace=True,
    )

    # compare inplace=True and inplace=False output
    pd.testing.assert_frame_equal(output_df, adata.var)

    # check output is complete
    for key in [
        "highly_variable",
        "means",
        "variances",
        "residual_variances",
        "highly_variable_rank",
        "highly_variable_nbatches",
        "highly_variable_intersection",
    ]:
        assert key in output_df.keys()

    # general checks on ranks, hvg flag and residual variance
    _check_pearson_hvg_columns(output_df, n_top_genes)

    # check intersection flag
    nbatches = len(np.unique(adata.obs["batch"]))
    assert output_df["highly_variable_intersection"].to_numpy().dtype is np.dtype(
        "bool"
    )
    assert np.sum(output_df["highly_variable_intersection"]) <= n_top_genes * nbatches
    assert np.all(output_df["highly_variable"][output_df.highly_variable_intersection])

    # check ranks (with batch_key these are the median of within-batch ranks)
    assert pd.api.types.is_float_dtype(output_df["highly_variable_rank"].dtype)

    # check nbatches
    assert output_df["highly_variable_nbatches"].to_numpy().dtype is np.dtype("int")
    assert np.min(output_df["highly_variable_nbatches"].to_numpy()) >= 0
    assert np.max(output_df["highly_variable_nbatches"].to_numpy()) <= nbatches

    # check subsetting
    if subset:
        assert len(output_df) == n_top_genes
    else:
        assert len(output_df) == n_genes


def test_highly_variable_genes_compare_to_seurat():
    seurat_hvg_info = pd.read_csv(FILE, sep=" ")

    pbmc = pbmc68k_reduced()
    pbmc.X = pbmc.raw.X
    pbmc.var_names_make_unique()

    sc.pp.normalize_per_cell(pbmc, counts_per_cell_after=1e4)
    sc.pp.log1p(pbmc)
    sc.pp.highly_variable_genes(
        pbmc, flavor="seurat", min_mean=0.0125, max_mean=3, min_disp=0.5, inplace=True
    )

    np.testing.assert_array_equal(
        seurat_hvg_info["highly_variable"], pbmc.var["highly_variable"]
    )

    # (still) Not equal to tolerance rtol=2e-05, atol=2e-05
    # np.testing.assert_allclose(4, 3.9999, rtol=2e-05, atol=2e-05)
    np.testing.assert_allclose(
        seurat_hvg_info["means"],
        pbmc.var["means"],
        rtol=2e-05,
        atol=2e-05,
    )
    np.testing.assert_allclose(
        seurat_hvg_info["dispersions"],
        pbmc.var["dispersions"],
        rtol=2e-05,
        atol=2e-05,
    )
    np.testing.assert_allclose(
        seurat_hvg_info["dispersions_norm"],
        pbmc.var["dispersions_norm"],
        rtol=2e-05,
        atol=2e-05,
    )


@needs.skmisc
def test_highly_variable_genes_compare_to_seurat_v3():
    ### test without batch
    seurat_hvg_info = pd.read_csv(FILE_V3)

    pbmc = pbmc3k()
    sc.pp.filter_cells(pbmc, min_genes=200)  # this doesnt do anything btw
    sc.pp.filter_genes(pbmc, min_cells=3)

    pbmc_dense = pbmc.copy()
    pbmc_dense.X = pbmc_dense.X.toarray()

    sc.pp.highly_variable_genes(pbmc, n_top_genes=1000, flavor="seurat_v3_paper")
    sc.pp.highly_variable_genes(pbmc_dense, n_top_genes=1000, flavor="seurat_v3_paper")

    np.testing.assert_allclose(
        seurat_hvg_info["variance"],
        pbmc.var["variances"],
        rtol=2e-05,
        atol=2e-05,
    )
    np.testing.assert_allclose(
        seurat_hvg_info["variance.standardized"],
        pbmc.var["variances_norm"],
        rtol=2e-05,
        atol=2e-05,
    )
    np.testing.assert_allclose(
        pbmc_dense.var["variances_norm"],
        pbmc.var["variances_norm"],
        rtol=2e-05,
        atol=2e-05,
    )

    ### test with batch
    # introduce a dummy "technical covariate"; this is used in Seurat's SelectIntegrationFeatures
    pbmc.obs["dummy_tech"] = "source_1"
    pbmc.obs.loc[pbmc.obs.index[500:1000], "dummy_tech"] = "source_2"
    pbmc.obs.loc[pbmc.obs.index[1000:1500], "dummy_tech"] = "source_3"
    pbmc.obs.loc[pbmc.obs.index[1500:2000], "dummy_tech"] = "source_4"
    pbmc.obs.loc[pbmc.obs.index[2000:], "dummy_tech"] = "source_5"

    seurat_v3_hvg = sc.pp.highly_variable_genes(
        pbmc,
        n_top_genes=2000,
        flavor="seurat_v3_paper",
        batch_key="dummy_tech",
        inplace=False,
    )

    # this is the scanpy implementation up until now
    seurat_v3_scanpy_legacy_hvg = sc.pp.highly_variable_genes(
        pbmc,
        n_top_genes=2000,
        flavor="seurat_v3_scanpy_legacy",
        batch_key="dummy_tech",
        inplace=False,
    )

    seurat_hvg_info_batch = pd.read_csv(FILE_V3_BATCH)
    seu = pd.Index(seurat_hvg_info_batch["x"].to_numpy())

    assert (
        len(seu.intersection(seurat_v3_hvg[seurat_v3_hvg.highly_variable].index)) / 2000
        > 0.95
    )
    assert not (
        len(
            seu.intersection(
                seurat_v3_scanpy_legacy_hvg[
                    seurat_v3_scanpy_legacy_hvg.highly_variable
                ].index
            )
        )
        / 2000
        > 0.95
    )


@needs.skmisc
def test_highly_variable_genes_seurat_v3_deprecation_warning():
    pbmc = pbmc3k()
    with pytest.warns(
        FutureWarning,
        match="The name flavor='seurat_v3' is deprecated, set to flavor='seurat_v3_scanpy_legacy'. Please pass this explicitly if you want to use this scanpy behaviour.",
    ):
        sc.pp.highly_variable_genes(pbmc, flavor="seurat_v3")


@needs.skmisc
def test_highly_variable_genes_seurat_v3_warning():
    pbmc = pbmc3k()[:200].copy()
    sc.pp.log1p(pbmc)
    with pytest.warns(
        UserWarning,
        match="`flavor='seurat_v3_paper'` expects raw count data, but non-integers were found.",
    ):
        sc.pp.highly_variable_genes(pbmc, flavor="seurat_v3_paper")


def test_filter_genes_dispersion_compare_to_seurat():
    seurat_hvg_info = pd.read_csv(FILE, sep=" ")

    pbmc = pbmc68k_reduced()
    pbmc.X = pbmc.raw.X
    pbmc.var_names_make_unique()

    sc.pp.normalize_per_cell(pbmc, counts_per_cell_after=1e4)
    sc.pp.filter_genes_dispersion(
        pbmc,
        flavor="seurat",
        log=True,
        subset=False,
        min_mean=0.0125,
        max_mean=3,
        min_disp=0.5,
    )

    np.testing.assert_array_equal(
        seurat_hvg_info["highly_variable"], pbmc.var["highly_variable"]
    )

    # (still) Not equal to tolerance rtol=2e-05, atol=2e-05:
    # np.testing.assert_allclose(4, 3.9999, rtol=2e-05, atol=2e-05)
    np.testing.assert_allclose(
        seurat_hvg_info["means"],
        pbmc.var["means"],
        rtol=2e-05,
        atol=2e-05,
    )
    np.testing.assert_allclose(
        seurat_hvg_info["dispersions"],
        pbmc.var["dispersions"],
        rtol=2e-05,
        atol=2e-05,
    )
    np.testing.assert_allclose(
        seurat_hvg_info["dispersions_norm"],
        pbmc.var["dispersions_norm"],
        rtol=2e-05,
        atol=2e-05,
    )


def test_highly_variable_genes_batches():
    adata = pbmc68k_reduced()
    adata[:100, :100].X = np.zeros((100, 100))

    adata.obs["batch"] = ["0" if i < 100 else "1" for i in range(adata.n_obs)]
    adata_1 = adata[adata.obs.batch.isin(["0"]), :]
    adata_2 = adata[adata.obs.batch.isin(["1"]), :]

    sc.pp.highly_variable_genes(
        adata,
        batch_key="batch",
        flavor="cell_ranger",
        n_top_genes=200,
    )

    sc.pp.filter_genes(adata_1, min_cells=1)
    sc.pp.filter_genes(adata_2, min_cells=1)
    hvg1 = sc.pp.highly_variable_genes(
        adata_1, flavor="cell_ranger", n_top_genes=200, inplace=False
    )
    assert hvg1 is not None
    hvg2 = sc.pp.highly_variable_genes(
        adata_2, flavor="cell_ranger", n_top_genes=200, inplace=False
    )
    assert hvg2 is not None

    np.testing.assert_allclose(
        adata.var["dispersions_norm"].iat[100],
        0.5 * hvg1["dispersions_norm"].iat[0] + 0.5 * hvg2["dispersions_norm"].iat[100],
        rtol=1.0e-7,
        atol=1.0e-7,
    )
    np.testing.assert_allclose(
        adata.var["dispersions_norm"].iat[101],
        0.5 * hvg1["dispersions_norm"].iat[1] + 0.5 * hvg2["dispersions_norm"].iat[101],
        rtol=1.0e-7,
        atol=1.0e-7,
    )
    np.testing.assert_allclose(
        adata.var["dispersions_norm"].iat[0],
        0.5 * hvg2["dispersions_norm"].iat[0],
        rtol=1.0e-7,
        atol=1.0e-7,
    )

    colnames = [
        "means",
        "dispersions",
        "dispersions_norm",
        "highly_variable",
    ]

    assert np.all(np.isin(colnames, hvg1.columns))


@needs.skmisc
def test_seurat_v3_mean_var_output_with_batchkey():
    pbmc = pbmc3k()
    pbmc.var_names_make_unique()
    n_cells = pbmc.shape[0]
    batch = np.zeros((n_cells), dtype=int)
    batch[1500:] = 1
    pbmc.obs["batch"] = batch

    # true_mean, true_var = _get_mean_var(pbmc.X)
    true_mean = np.mean(pbmc.X.toarray(), axis=0)
    true_var = np.var(pbmc.X.toarray(), axis=0, dtype=np.float64, ddof=1)

    result_df = sc.pp.highly_variable_genes(
        pbmc,
        batch_key="batch",
        flavor="seurat_v3_paper",
        n_top_genes=4000,
        inplace=False,
    )
    np.testing.assert_allclose(true_mean, result_df["means"], rtol=2e-05, atol=2e-05)
    np.testing.assert_allclose(true_var, result_df["variances"], rtol=2e-05, atol=2e-05)


def test_cellranger_n_top_genes_warning():
    X = np.random.poisson(2, (100, 30))
    adata = sc.AnnData(X)
    sc.pp.normalize_total(adata)
    sc.pp.log1p(adata)

    with pytest.warns(
        UserWarning,
        match="`n_top_genes` > number of normalized dispersions, returning all genes with normalized dispersions.",
    ):
        sc.pp.highly_variable_genes(adata, n_top_genes=1000, flavor="cell_ranger")


@pytest.mark.parametrize("flavor", ["seurat", "cell_ranger"])
@pytest.mark.parametrize("subset", [True, False], ids=["subset", "full"])
@pytest.mark.parametrize("inplace", [True, False], ids=["inplace", "copy"])
def test_highly_variable_genes_subset_inplace_consistency(flavor, subset, inplace):
    adata = sc.datasets.blobs(n_observations=20, n_variables=80, random_state=0)
    adata.X = np.abs(adata.X).astype(int)

    if flavor == "seurat" or flavor == "cell_ranger":
        sc.pp.normalize_total(adata, target_sum=1e4)
        sc.pp.log1p(adata)

    elif flavor == "seurat_v3":
        pass

    else:
        raise ValueError(f"Unknown flavor {flavor}")

    n_genes = adata.shape[1]

    output_df = sc.pp.highly_variable_genes(
        adata,
        flavor=flavor,
        n_top_genes=15,
        subset=subset,
        inplace=inplace,
    )

    assert (output_df is None) == inplace
    assert len(adata.var if inplace else output_df) == (15 if subset else n_genes)
