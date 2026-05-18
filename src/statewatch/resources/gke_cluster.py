"""``google_container_cluster`` (GKE) normalization — the fourth and final v0.1 type.

Follows the Phase 3 per-type pattern. ``parent_refs``: network, subnetwork (region-
qualified via the shared resolver), and the default node pool's service account.
"""

from __future__ import annotations

from typing import Any

from statewatch.normalizer import (
    GCP_PROVIDER,
    Resource,
    network_ref,
    region_from_zone,
    service_account_ref,
    short_name,
    subnetwork_ref_from_attr,
)

RESOURCE_TYPE = "google_container_cluster"


def _region_from_location(location: str | None) -> str:
    """A GKE ``location`` is a region (``us-central1``) or a zone (``us-central1-a``)."""
    loc = short_name(location or "")
    if not loc:
        return ""
    # zones have three dash-separated parts; regions have two.
    return region_from_zone(loc) if loc.count("-") >= 2 else loc


def _first(value: Any) -> dict[str, Any]:
    """tfstate nests single blocks as 1-element lists; CAI uses a plain object."""
    if isinstance(value, list):
        return value[0] if value else {}
    return value if isinstance(value, dict) else {}


def _parent_refs(
    *, project: str, location: str | None, network: str | None,
    subnetwork: str | None, service_account: str | None,
) -> tuple[str, ...]:
    refs: list[str] = []
    if network:
        refs.append(network_ref(project, network))
    if subnetwork:
        refs.append(
            subnetwork_ref_from_attr(project, _region_from_location(location), subnetwork)
        )
    if service_account and service_account != "default":
        refs.append(service_account_ref(project, service_account))
    return tuple(refs)


def _attributes(
    *, location: str | None, network: str | None, subnetwork: str | None,
    node_machine_type: str | None, service_account: str | None,
    private_nodes: bool | None, release_channel: str | None,
    master_version: str | None,
) -> dict[str, Any]:
    return {
        "location": short_name(location) if location else None,
        "network": short_name(network) if network else None,
        "subnetwork": short_name(subnetwork) if subnetwork else None,
        "node_machine_type": node_machine_type,
        "service_account": service_account,
        "private_nodes": bool(private_nodes),
        "release_channel": (release_channel or "").upper() or None,
        "master_version": master_version,
    }


def normalize_from_tfstate(
    attrs: dict[str, Any],
    *,
    project: str,
    terraform_address: str | None = None,
    depends_on: tuple[str, ...] = (),
) -> Resource:
    name = attrs.get("name", "")
    location = attrs.get("location")
    network = attrs.get("network")
    subnetwork = attrs.get("subnetwork")
    node_config = _first(attrs.get("node_config"))
    private_cfg = _first(attrs.get("private_cluster_config"))
    release = _first(attrs.get("release_channel"))
    return Resource(
        resource_id=f"projects/{project}/locations/{short_name(location) or ''}/clusters/{name}",
        resource_type=RESOURCE_TYPE,
        provider=GCP_PROVIDER,
        name=name,
        parent_refs=_parent_refs(
            project=project, location=location, network=network,
            subnetwork=subnetwork, service_account=node_config.get("service_account"),
        ),
        attributes=_attributes(
            location=location, network=network, subnetwork=subnetwork,
            node_machine_type=node_config.get("machine_type"),
            service_account=node_config.get("service_account"),
            private_nodes=private_cfg.get("enable_private_nodes"),
            release_channel=release.get("channel"),
            master_version=attrs.get("min_master_version") or attrs.get("master_version"),
        ),
        source="terraform",
        terraform_address=terraform_address,
        depends_on=tuple(depends_on),
    )


def normalize_from_cai(asset: dict[str, Any], *, project: str) -> Resource:
    data = (asset.get("resource") or {}).get("data") or {}
    name = data.get("name", "")
    location = data.get("location")
    network = data.get("network")
    subnetwork = data.get("subnetwork")
    node_config = data.get("nodeConfig") or {}
    private_cfg = data.get("privateClusterConfig") or {}
    release = data.get("releaseChannel") or {}
    return Resource(
        resource_id=f"projects/{project}/locations/{short_name(location) or ''}/clusters/{name}",
        resource_type=RESOURCE_TYPE,
        provider=GCP_PROVIDER,
        name=name,
        parent_refs=_parent_refs(
            project=project, location=location, network=network,
            subnetwork=subnetwork, service_account=node_config.get("serviceAccount"),
        ),
        attributes=_attributes(
            location=location, network=network, subnetwork=subnetwork,
            node_machine_type=node_config.get("machineType"),
            service_account=node_config.get("serviceAccount"),
            private_nodes=private_cfg.get("enablePrivateNodes"),
            release_channel=release.get("channel"),
            master_version=data.get("currentMasterVersion"),
        ),
        source="live",
    )
