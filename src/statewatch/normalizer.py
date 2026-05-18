"""Resource normalization — shared core.

Defines the common :class:`Resource` shape everything downstream consumes (the differ and
the dependency graph) plus the shared GCP id/attribute helpers. Per-resource-type
normalization lives in :mod:`statewatch.resources` (one module per type); this module is
deliberately type-agnostic so those modules can depend on it without a cycle.

Canonical resource ids are the cross-source join key *and* the graph node id. Every
producer (a resource's own normalization, and another resource's ``parent_refs``) must
derive the **same** id for the same thing, or the graph won't unify. The id builders here
are the single source of truth for that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GCP_PROVIDER = "gcp"

# We normalize by *whitelisting* comparable fields into a canonical attribute bag per
# resource type. Server-assigned / generated keys (ids, self_link, *_fingerprint,
# creation_timestamp, terraform_labels, …) are never copied in, so they can't drift.


@dataclass(frozen=True)
class Resource:
    """A single infrastructure resource, normalized across state sources.

    Frozen so instances are hashable and usable directly as graph nodes.

    Attributes:
        resource_id: Stable cross-source identity and graph node id (see id builders
            below). The same logical resource must yield the same id from ``.tfstate``,
            from CAI, and from another resource's ``parent_refs``.
        resource_type: Terraform-style type name, e.g. ``"google_compute_instance"``.
        provider: Cloud provider key, e.g. ``"gcp"``.
        name: Human-friendly label for output (the resource's GCP name).
        parent_refs: ``resource_id`` values this resource depends on — directed
            ``this --depends-on--> ref`` edges in the graph.
        attributes: Normalized, comparable attribute bag the differ walks.
        source: Which side this came from — ``"terraform"`` or ``"live"``.
        terraform_address: Terraform resource address when from state, else ``None``.
            Used to resolve ``depends_on`` / manual-edge targets to graph nodes.
        depends_on: Explicit Terraform ``depends_on`` targets (Terraform addresses);
            become *automatic* graph edges. Empty for live-state resources.
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
# Shared scalar helpers
# --------------------------------------------------------------------------------------


def short_name(value: Any) -> Any:
    """Last path segment of a GCP URL/path-style reference; pass non-strings through."""
    if not isinstance(value, str):
        return value
    return value.rstrip("/").rsplit("/", 1)[-1]


def region_from_zone(zone: str | None) -> str:
    """``us-central1-a`` -> ``us-central1``. Empty string if unknown."""
    z = short_name(zone or "")
    if not z or "-" not in z:
        return ""
    return z.rsplit("-", 1)[0]


def kv_pairs_to_dict(pairs: Any) -> dict[str, Any]:
    """Canonicalize ``[{"key":..,"value":..}, ...]`` (or a mapping) to a plain dict.

    CAI returns instance metadata as ``items: [{key, value}]``; tfstate may use either
    shape depending on schema version.
    """
    if isinstance(pairs, dict):
        return dict(pairs)
    result: dict[str, Any] = {}
    if isinstance(pairs, list):
        for item in pairs:
            if isinstance(item, dict) and "key" in item:
                result[item["key"]] = item.get("value")
    return result


# --------------------------------------------------------------------------------------
# Canonical id builders — the single source of truth for graph node identity
# --------------------------------------------------------------------------------------


def instance_id(project: str, zone: str, name: str) -> str:
    return f"projects/{project}/zones/{short_name(zone)}/instances/{name}"


def subnetwork_id(project: str, region: str, name: str) -> str:
    return f"projects/{project}/regions/{short_name(region)}/subnetworks/{name}"


def firewall_id(project: str, name: str) -> str:
    return f"projects/{project}/global/firewalls/{name}"


def network_ref(project: str, network: str) -> str:
    return f"projects/{project}/global/networks/{short_name(network)}"


def service_account_ref(project: str, email: str) -> str:
    return f"projects/{project}/serviceAccounts/{email}"


def subnetwork_ref_from_attr(project: str, region_hint: str, subnetwork: str) -> str:
    """Resolve a compute-instance ``subnetwork`` attribute to a canonical subnet id.

    Accepts a full URL, a ``projects/x/regions/y/subnetworks/z`` path, or a bare name.
    For the bare-name case the region is otherwise ambiguous; a GCP instance's subnet is
    always in the instance's own region, so ``region_hint`` (derived from the instance
    zone) supplies it. This makes an instance's inferred subnet ref equal the subnet
    resource's own ``resource_id`` (KNOWN_ISSUES #1 — resolved here).
    """
    s = subnetwork.split("compute/v1/")[-1] if "compute/v1/" in subnetwork else subnetwork
    s = s.strip("/")
    if s.startswith("projects/") and "/subnetworks/" in s:
        parts = s.split("/")
        # projects/<p>/regions/<r>/subnetworks/<n>
        proj = parts[1] if len(parts) > 1 else project
        region = parts[3] if "regions" in parts and len(parts) > 3 else region_hint
        name = parts[-1]
        return subnetwork_id(proj, region, name)
    return subnetwork_id(project, region_hint, short_name(s))
