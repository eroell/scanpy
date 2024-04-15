from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from warnings import warn

import numpy as np
from anndata import AnnData

from ... import logging as logg
from ..._compat import DaskArray
from ..._utils import (
    Empty,
    _doc_params,
    _empty,
    axis_sum,
    check_nonnegative_integers,
    clip_array,
    view_to_actual,
)
from ...experimental._docs import (
    doc_adata,
    doc_check_values,
    doc_copy,
    doc_dist_params,
    doc_inplace,
    doc_layer,
    doc_pca_chunk,
)
from ...get import _get_obs_rep, _set_obs_rep
from ...preprocessing._docs import doc_mask_var_hvg
from ...preprocessing._pca import _handle_mask_var, pca

if TYPE_CHECKING:
    from collections.abc import Mapping

    from scipy.sparse import spmatrix


def _pearson_residuals(
    X: np.ndarray | spmatrix | DaskArray,
    theta: np.float64,
    clip: np.float64,
    check_values: bool,
    copy: bool = False,
) -> np.ndarray | spmatrix | DaskArray:
    X = X.copy() if copy else X

    # check theta
    if theta <= 0:
        # TODO: would "underdispersion" with negative theta make sense?
        # then only theta=0 were undefined..
        raise ValueError("Pearson residuals require theta > 0")
    # prepare clipping
    if clip is None:
        n = X.shape[0]
        clip = np.sqrt(n)
    if clip < 0:
        raise ValueError("Pearson residuals require `clip>=0` or `clip=None`.")

    if check_values and not check_nonnegative_integers(X):
        warn(
            "`normalize_pearson_residuals()` expects raw count data, but non-integers were found.",
            UserWarning,
        )

    sums_genes = axis_sum(X, axis=0, dtype=np.float64).reshape(1, -1)
    sums_cells = axis_sum(X, axis=1, dtype=np.float64).reshape(-1, 1)
    sum_total = sums_genes.sum()

    # TODO: Consider deduplicating computations below which are similarly required in _highly_variable_genes?
    if not isinstance(X, DaskArray):
        mu = np.array(sums_cells @ sums_genes / sum_total)
        diff = np.array(X - mu)
    else:
        mu = sums_cells @ sums_genes / sum_total
        diff = (
            X - mu
        )  # here, potentially a dask sparse array and a dense sparse array are subtracted from each other
        diff = diff.map_blocks(np.array, dtype=np.float64)
    residuals = diff / np.sqrt(mu + mu**2 / theta)

    # residuals are dense, hence no circumventing for sparse-in-dask needed
    residuals = clip_array(residuals, -clip, clip)

    return residuals


@_doc_params(
    adata=doc_adata,
    dist_params=doc_dist_params,
    check_values=doc_check_values,
    layer=doc_layer,
    inplace=doc_inplace,
    copy=doc_copy,
)
def normalize_pearson_residuals(
    adata: AnnData,
    *,
    theta: float = 100,
    clip: float | None = None,
    check_values: bool = True,
    layer: str | None = None,
    inplace: bool = True,
    copy: bool = False,
) -> AnnData | dict[str, np.ndarray] | None:
    """\
    Applies analytic Pearson residual normalization, based on [Lause21]_.

    The residuals are based on a negative binomial offset model with overdispersion
    `theta` shared across genes. By default, residuals are clipped to `sqrt(n_obs)`
    and overdispersion `theta=100` is used.

    Expects raw count input.

    Params
    ------
    {adata}
    {dist_params}
    {check_values}
    {layer}
    {inplace}
    {copy}

    Returns
    -------
    If `inplace=True`, `adata.X` or the selected layer in `adata.layers` is updated
    with the normalized values. `adata.uns` is updated with the following fields.
    If `inplace=False`, the same fields are returned as dictionary with the
    normalized values in `results_dict['X']`.

    `.uns['pearson_residuals_normalization']['theta']`
         The used value of the overdisperion parameter theta.
    `.uns['pearson_residuals_normalization']['clip']`
         The used value of the clipping parameter.
    `.uns['pearson_residuals_normalization']['computed_on']`
         The name of the layer on which the residuals were computed.
    """

    if copy:
        if not inplace:
            raise ValueError("`copy=True` cannot be used with `inplace=False`.")
        adata = adata.copy()

    view_to_actual(adata)
    X = _get_obs_rep(adata, layer=layer)
    computed_on = layer if layer else "adata.X"

    msg = f"computing analytic Pearson residuals on {computed_on}"
    start = logg.info(msg)

    residuals = _pearson_residuals(X, theta, clip, check_values, copy=~inplace)
    settings_dict = dict(theta=theta, clip=clip, computed_on=computed_on)

    if inplace:
        _set_obs_rep(adata, residuals, layer=layer)
        adata.uns["pearson_residuals_normalization"] = settings_dict
    else:
        results_dict = dict(X=residuals, **settings_dict)

    logg.info("    finished ({time_passed})", time=start)

    if copy:
        return adata
    elif not inplace:
        return results_dict


@_doc_params(
    adata=doc_adata,
    dist_params=doc_dist_params,
    pca_chunk=doc_pca_chunk,
    mask_var_hvg=doc_mask_var_hvg,
    check_values=doc_check_values,
    inplace=doc_inplace,
)
def normalize_pearson_residuals_pca(
    adata: AnnData,
    *,
    theta: float = 100,
    clip: float | None = None,
    n_comps: int | None = 50,
    random_state: float = 0,
    kwargs_pca: Mapping[str, Any] = MappingProxyType({}),
    mask_var: np.ndarray | str | None | Empty = _empty,
    use_highly_variable: bool | None = None,
    check_values: bool = True,
    inplace: bool = True,
) -> AnnData | None:
    """\
    Applies analytic Pearson residual normalization and PCA, based on [Lause21]_.

    The residuals are based on a negative binomial offset model with overdispersion
    `theta` shared across genes. By default, residuals are clipped to `sqrt(n_obs)`,
    overdispersion `theta=100` is used, and PCA is run with 50 components.

    Operates on the subset of highly variable genes in `adata.var['highly_variable']`
    by default. Expects raw count input.

    Params
    ------
    {adata}
    {dist_params}
    {pca_chunk}
    {mask_var_hvg}
    {check_values}
    {inplace}

    Returns
    -------
    If `inplace=False`, returns the Pearson residual-based PCA results (as :class:`~anndata.AnnData`
    object). If `inplace=True`, updates `adata` with the following fields:

    `.uns['pearson_residuals_normalization']['pearson_residuals_df']`
        The subset of highly variable genes, normalized by Pearson residuals.
    `.uns['pearson_residuals_normalization']['theta']`
        The used value of the overdisperion parameter theta.
    `.uns['pearson_residuals_normalization']['clip']`
        The used value of the clipping parameter.

    `.obsm['X_pca']`
        PCA representation of data after gene selection (if applicable) and Pearson
        residual normalization.
    `.varm['PCs']`
        The principal components containing the loadings. When `inplace=True` and
        `use_highly_variable=True`, this will contain empty rows for the genes not
        selected.
    `.uns['pca']['variance_ratio']`
        Ratio of explained variance.
    `.uns['pca']['variance']`
        Explained variance, equivalent to the eigenvalues of the covariance matrix.
    """

    # Unify new mask argument and deprecated use_highly_varible argument
    _, mask_var = _handle_mask_var(adata, mask_var, use_highly_variable)
    del use_highly_variable

    if mask_var is not None:
        adata_sub = adata[:, mask_var].copy()
        adata_pca = AnnData(
            adata_sub.X.copy(), obs=adata_sub.obs[[]], var=adata_sub.var[[]]
        )
    else:
        adata_pca = AnnData(adata.X.copy(), obs=adata.obs[[]], var=adata.var[[]])

    normalize_pearson_residuals(
        adata_pca, theta=theta, clip=clip, check_values=check_values
    )
    pca(adata_pca, n_comps=n_comps, random_state=random_state, **kwargs_pca)
    n_comps = adata_pca.obsm["X_pca"].shape[1]  # might be None

    if inplace:
        norm_settings = adata_pca.uns["pearson_residuals_normalization"]
        norm_dict = dict(**norm_settings, pearson_residuals_df=adata_pca.to_df())
        if mask_var is not None:
            adata.varm["PCs"] = np.zeros(shape=(adata.n_vars, n_comps))
            adata.varm["PCs"][mask_var] = adata_pca.varm["PCs"]
        else:
            adata.varm["PCs"] = adata_pca.varm["PCs"]
        adata.uns["pca"] = adata_pca.uns["pca"]
        adata.uns["pearson_residuals_normalization"] = norm_dict
        adata.obsm["X_pca"] = adata_pca.obsm["X_pca"]
        return None
    else:
        return adata_pca
