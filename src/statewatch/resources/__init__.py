"""Per-resource-type normalizers + a small dispatch registry.

One module per Terraform type. ``normalize_tf`` / ``normalize_cai`` route by type so the
loader and adapter stay type-agnostic; unknown types return ``None`` (skipped, not an
error — out-of-scope resource types in a state file are normal).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from statewatch.normalizer import Resource
from statewatch.resources import compute_instance, firewall, subnetwork

_TF: dict[str, Callable[..., Resource]] = {
    compute_instance.RESOURCE_TYPE: compute_instance.normalize_from_tfstate,
    firewall.RESOURCE_TYPE: firewall.normalize_from_tfstate,
    subnetwork.RESOURCE_TYPE: subnetwork.normalize_from_tfstate,
}

_CAI_ASSET_TO_TYPE = {
    "compute.googleapis.com/Instance": compute_instance.RESOURCE_TYPE,
    "compute.googleapis.com/Firewall": firewall.RESOURCE_TYPE,
    "compute.googleapis.com/Subnetwork": subnetwork.RESOURCE_TYPE,
}

_CAI: dict[str, Callable[..., Resource]] = {
    compute_instance.RESOURCE_TYPE: compute_instance.normalize_from_cai,
    firewall.RESOURCE_TYPE: firewall.normalize_from_cai,
    subnetwork.RESOURCE_TYPE: subnetwork.normalize_from_cai,
}

#: Terraform type names statewatch normalizes in v0.1 (Phase 3 scope).
SUPPORTED_RESOURCE_TYPES = frozenset(_TF)

#: CAI asset types statewatch fetches live state for.
SUPPORTED_CAI_ASSET_TYPES = frozenset(_CAI_ASSET_TO_TYPE)


def normalize_tf(
    resource_type: str,
    attrs: dict[str, Any],
    *,
    project: str,
    terraform_address: str | None = None,
    depends_on: tuple[str, ...] = (),
) -> Resource | None:
    fn = _TF.get(resource_type)
    if fn is None:
        return None
    return fn(
        attrs,
        project=project,
        terraform_address=terraform_address,
        depends_on=depends_on,
    )


def normalize_cai(asset: dict[str, Any], *, project: str) -> Resource | None:
    asset_type = asset.get("asset_type") or asset.get("assetType")
    rtype = _CAI_ASSET_TO_TYPE.get(asset_type or "")
    if rtype is None:
        return None
    return _CAI[rtype](asset, project=project)
