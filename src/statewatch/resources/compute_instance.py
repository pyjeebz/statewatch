"""``google_compute_instance`` normalization.

Moved out of ``normalizer.py`` in Phase 3 (behavior-preserving) when a second and third
resource type made the per-type split earn its place.
"""

from __future__ import annotations

from typing import Any

from statewatch.normalizer import (
    GCP_PROVIDER,
    Resource,
    instance_id,
    kv_pairs_to_dict,
    network_ref,
    region_from_zone,
    service_account_ref,
    short_name,
    subnetwork_ref_from_attr,
)

RESOURCE_TYPE = "google_compute_instance"


def _parent_refs(
    *,
    project: str,
    zone: str | None,
    network: str | None,
    subnetwork: str | None,
    service_account: str | None,
) -> tuple[str, ...]:
    refs: list[str] = []
    if network:
        refs.append(network_ref(project, network))
    if subnetwork:
        refs.append(subnetwork_ref_from_attr(project, region_from_zone(zone), subnetwork))
    if service_account:
        refs.append(service_account_ref(project, service_account))
    return tuple(refs)


def _attributes(
    *,
    machine_type: str | None,
    zone: str | None,
    network: str | None,
    subnetwork: str | None,
    network_ip: str | None,
    external_ip: str | None,
    service_account: str | None,
    scopes: list[str] | None,
    metadata: dict[str, Any],
    labels: dict[str, Any],
    tags: list[str] | None,
    deletion_protection: bool | None,
    can_ip_forward: bool | None,
    preemptible: bool | None,
) -> dict[str, Any]:
    return {
        "machine_type": short_name(machine_type) if machine_type else None,
        "zone": short_name(zone) if zone else None,
        "network": short_name(network) if network else None,
        "subnetwork": short_name(subnetwork) if subnetwork else None,
        "network_ip": network_ip,
        "external_ip": external_ip,
        "service_account": service_account,
        "scopes": sorted(scopes) if scopes else [],
        "metadata": dict(sorted(metadata.items())),
        "labels": dict(sorted(labels.items())),
        "tags": sorted(tags) if tags else [],
        "deletion_protection": bool(deletion_protection),
        "can_ip_forward": bool(can_ip_forward),
        "preemptible": bool(preemptible),
    }


def normalize_from_tfstate(
    attrs: dict[str, Any],
    *,
    project: str,
    terraform_address: str | None = None,
    depends_on: tuple[str, ...] = (),
) -> Resource:
    """Normalize one ``google_compute_instance`` entry from parsed ``.tfstate``."""
    name = attrs.get("name", "")
    zone = attrs.get("zone")

    net_ifaces = attrs.get("network_interface") or []
    first_iface = net_ifaces[0] if net_ifaces else {}
    network = first_iface.get("network")
    subnetwork = first_iface.get("subnetwork")
    network_ip = first_iface.get("network_ip")
    access_configs = first_iface.get("access_config") or []
    external_ip = access_configs[0].get("nat_ip") if access_configs else None

    sa_block_list = attrs.get("service_account") or []
    sa_block = sa_block_list[0] if sa_block_list else {}
    service_account = sa_block.get("email")
    scopes = list(sa_block.get("scopes") or [])

    scheduling_list = attrs.get("scheduling") or []
    scheduling = scheduling_list[0] if scheduling_list else {}
    preemptible = scheduling.get("preemptible")

    metadata = kv_pairs_to_dict(attrs.get("metadata"))
    labels = dict(attrs.get("labels") or {})
    tags = list(attrs.get("tags") or [])

    return Resource(
        resource_id=instance_id(project, zone or "", name),
        resource_type=RESOURCE_TYPE,
        provider=GCP_PROVIDER,
        name=name,
        parent_refs=_parent_refs(
            project=project,
            zone=zone,
            network=network,
            subnetwork=subnetwork,
            service_account=service_account,
        ),
        attributes=_attributes(
            machine_type=attrs.get("machine_type"),
            zone=zone,
            network=network,
            subnetwork=subnetwork,
            network_ip=network_ip,
            external_ip=external_ip,
            service_account=service_account,
            scopes=scopes,
            metadata=metadata,
            labels=labels,
            tags=tags,
            deletion_protection=attrs.get("deletion_protection"),
            can_ip_forward=attrs.get("can_ip_forward"),
            preemptible=preemptible,
        ),
        source="terraform",
        terraform_address=terraform_address,
        depends_on=tuple(depends_on),
    )


def normalize_from_cai(asset: dict[str, Any], *, project: str) -> Resource:
    """Normalize one CAI ``compute.googleapis.com/Instance`` asset."""
    data = (asset.get("resource") or {}).get("data") or {}
    name = data.get("name", "")
    zone = data.get("zone", "")

    net_ifaces = data.get("networkInterfaces") or []
    first_iface = net_ifaces[0] if net_ifaces else {}
    network = first_iface.get("network")
    subnetwork = first_iface.get("subnetwork")
    network_ip = first_iface.get("networkIP")
    access_configs = first_iface.get("accessConfigs") or []
    external_ip = access_configs[0].get("natIP") if access_configs else None

    sa_list = data.get("serviceAccounts") or []
    sa = sa_list[0] if sa_list else {}
    service_account = sa.get("email")
    scopes = list(sa.get("scopes") or [])

    scheduling = data.get("scheduling") or {}
    preemptible = scheduling.get("preemptible")

    metadata = kv_pairs_to_dict((data.get("metadata") or {}).get("items"))
    labels = dict(data.get("labels") or {})
    tags = list((data.get("tags") or {}).get("items") or [])

    return Resource(
        resource_id=instance_id(project, zone, name),
        resource_type=RESOURCE_TYPE,
        provider=GCP_PROVIDER,
        name=name,
        parent_refs=_parent_refs(
            project=project,
            zone=zone,
            network=network,
            subnetwork=subnetwork,
            service_account=service_account,
        ),
        attributes=_attributes(
            machine_type=data.get("machineType"),
            zone=zone,
            network=network,
            subnetwork=subnetwork,
            network_ip=network_ip,
            external_ip=external_ip,
            service_account=service_account,
            scopes=scopes,
            metadata=metadata,
            labels=labels,
            tags=tags,
            deletion_protection=data.get("deletionProtection"),
            can_ip_forward=data.get("canIpForward"),
            preemptible=preemptible,
        ),
        source="live",
    )
