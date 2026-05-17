"""Resource normalization.

Defines the common :class:`Resource` shape that everything downstream consumes — the
differ today, and the dependency graph in Phase 2. Both the Terraform state side and the
live cloud (Cloud Asset Inventory) side are mapped into this shape so they can be paired by
``resource_id`` and compared attribute-by-attribute.

Phase 1 only handles ``google_compute_instance``. Per-resource-type normalizers will move
into ``statewatch/resources/`` in Phase 3 when more types arrive; for now the compute
instance logic lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GCP_PROVIDER = "gcp"

# We normalize by *whitelisting* comparable fields into a canonical attribute bag (see
# ``_common_compute_instance_attributes``). Server-assigned / generated keys — ``id``,
# ``instance_id``, ``self_link``, ``*_fingerprint``, ``cpu_platform``, ``creation_timestamp``,
# ``terraform_labels``, etc. — are simply never copied in, so they can't cause spurious drift.


@dataclass(frozen=True)
class Resource:
    """A single infrastructure resource, normalized across state sources.

    The dataclass is frozen so instances are hashable and can be used directly as graph
    nodes in Phase 2.

    Attributes:
        resource_id: Stable identity used to join the same resource across sources. For a
            compute instance this is ``projects/<project>/zones/<zone>/instances/<name>``.
            The normalizer's job is to derive the *same* id from a ``.tfstate`` entry and
            from a CAI asset.
        resource_type: Terraform-style type name, e.g. ``"google_compute_instance"``.
        provider: Cloud provider key, e.g. ``"gcp"``. Lets the Phase 2 graph hold nodes
            from multiple providers.
        name: Human-friendly label for output (the resource's GCP name).
        parent_refs: ``resource_id`` values this resource depends on. These become directed
            edges (this --depends-on--> ref) in the Phase 2 graph. Populated in Phase 1
            from unambiguous attribute references (network, subnetwork, service account) to
            exercise the design even though nothing consumes it yet.
        attributes: Normalized, comparable attribute bag the differ walks. Provider noise
            (fingerprints, generated timestamps, server-assigned ids) is removed here.
        source: Which side this came from — ``"terraform"`` or ``"live"``.
        terraform_address: The Terraform resource address
            (e.g. ``module.x.google_compute_instance.api[0]``) when this came from state,
            else ``None``. Phase 2 uses it to resolve ``depends_on`` / manual-edge targets
            (which speak Terraform addresses) to canonical ``resource_id`` graph nodes, and
            as human-readable node metadata.
        depends_on: Explicit Terraform ``depends_on`` / reference targets from the
            ``.tfstate`` instance, as Terraform addresses. Phase 2 turns these into
            *automatic* graph edges. Empty for live-state resources.
    """

    resource_id: str
    resource_type: str
    provider: str
    name: str
    parent_refs: tuple[str, ...] = ()
    attributes: dict[str, Any] = field(default_factory=dict)
    source: str = "terraform"
    terraform_address: str | None = None
    depends_on: tuple[str, ...] = ()


# --------------------------------------------------------------------------------------
# google_compute_instance
# --------------------------------------------------------------------------------------


def _short_name(value: str) -> str:
    """Return the last path segment of a GCP URL/path-style reference, else the value."""
    if not isinstance(value, str):
        return value
    return value.rstrip("/").rsplit("/", 1)[-1]


def _compute_instance_id(project: str, zone: str, name: str) -> str:
    return f"projects/{project}/zones/{_short_name(zone)}/instances/{name}"


def _compute_instance_parent_refs(
    *, project: str, network: str | None, subnetwork: str | None, service_account: str | None
) -> tuple[str, ...]:
    """Derive parent ``resource_id`` references from a compute instance's attributes.

    These mirror the identifiers the Phase 2 graph will assign to the referenced
    resources, so edges line up. Anything missing is simply omitted.
    """
    refs: list[str] = []
    if network:
        refs.append(f"projects/{project}/global/networks/{_short_name(network)}")
    if subnetwork:
        # Subnetwork refs are regional; we keep the region segment when present.
        refs.append(_normalize_subnetwork_ref(project, subnetwork))
    if service_account:
        refs.append(f"projects/{project}/serviceAccounts/{service_account}")
    return tuple(refs)


def _normalize_subnetwork_ref(project: str, subnetwork: str) -> str:
    # Accept full URLs, "projects/x/regions/y/subnetworks/z", or a bare name.
    s = subnetwork.split("compute/v1/")[-1] if "compute/v1/" in subnetwork else subnetwork
    s = s.strip("/")
    if s.startswith("projects/"):
        return s
    return f"projects/{project}/subnetworks/{_short_name(s)}"


def _kv_pairs_to_dict(pairs: Any) -> dict[str, Any]:
    """Normalize a list of ``{"key": ..., "value": ...}`` items into a plain dict.

    CAI returns instance metadata as ``items: [{key, value}, ...]``; tfstate may use either
    a plain mapping or the same list shape depending on schema version. We canonicalize to
    a dict so the differ compares like with like.
    """
    if isinstance(pairs, dict):
        return dict(pairs)
    result: dict[str, Any] = {}
    if isinstance(pairs, list):
        for item in pairs:
            if isinstance(item, dict) and "key" in item:
                result[item["key"]] = item.get("value")
    return result


def _common_compute_instance_attributes(
    *,
    machine_type: str | None,
    zone: str | None,
    status: str | None,
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
    """Build the canonical, comparable attribute bag shared by both sources."""
    return {
        "machine_type": _short_name(machine_type) if machine_type else None,
        "zone": _short_name(zone) if zone else None,
        "status": status,
        "network": _short_name(network) if network else None,
        "subnetwork": _short_name(subnetwork) if subnetwork else None,
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


def normalize_compute_instance_from_tfstate(
    attrs: dict[str, Any],
    *,
    project: str,
    terraform_address: str | None = None,
    depends_on: tuple[str, ...] = (),
) -> Resource:
    """Normalize one ``google_compute_instance`` entry from a parsed ``.tfstate``.

    ``attrs`` is the ``attributes`` dict of a single resource instance as produced by
    :func:`statewatch.tfstate.extract_compute_instances`. ``terraform_address`` and
    ``depends_on`` come from the same :class:`~statewatch.tfstate.TerraformResourceInstance`
    and are carried onto the :class:`Resource` for Phase 2 graph construction.
    """
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

    metadata = _kv_pairs_to_dict(attrs.get("metadata"))
    labels = dict(attrs.get("labels") or {})
    tags = list(attrs.get("tags") or [])

    resource_id = _compute_instance_id(project, zone or "", name)
    attributes = _common_compute_instance_attributes(
        machine_type=attrs.get("machine_type"),
        zone=zone,
        status=attrs.get("current_status"),
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
    )
    # status is not meaningful from tfstate; drop it so it never spuriously diffs.
    attributes.pop("status", None)

    return Resource(
        resource_id=resource_id,
        resource_type="google_compute_instance",
        provider=GCP_PROVIDER,
        name=name,
        parent_refs=_compute_instance_parent_refs(
            project=project,
            network=network,
            subnetwork=subnetwork,
            service_account=service_account,
        ),
        attributes=attributes,
        source="terraform",
        terraform_address=terraform_address,
        depends_on=tuple(depends_on),
    )


def normalize_compute_instance_from_cai(asset: dict[str, Any], *, project: str) -> Resource:
    """Normalize one Cloud Asset Inventory asset of type ``compute.googleapis.com/Instance``.

    ``asset`` is expected to look like a CAI ``Asset`` rendered to a dict: it has a
    ``name`` (full resource path) and a ``resource.data`` mapping holding the GCP Instance
    REST representation.
    """
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

    metadata = _kv_pairs_to_dict((data.get("metadata") or {}).get("items"))
    labels = dict(data.get("labels") or {})
    tags = list((data.get("tags") or {}).get("items") or [])

    resource_id = _compute_instance_id(project, zone, name)
    attributes = _common_compute_instance_attributes(
        machine_type=data.get("machineType"),
        zone=zone,
        status=data.get("status"),
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
    )
    attributes.pop("status", None)

    return Resource(
        resource_id=resource_id,
        resource_type="google_compute_instance",
        provider=GCP_PROVIDER,
        name=name,
        parent_refs=_compute_instance_parent_refs(
            project=project,
            network=network,
            subnetwork=subnetwork,
            service_account=service_account,
        ),
        attributes=attributes,
        source="live",
    )
