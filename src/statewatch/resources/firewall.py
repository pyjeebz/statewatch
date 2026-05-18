"""``google_compute_firewall`` normalization.

Security-critical and the headline use case for impact analysis: a firewall change is
CRITICAL severity (classifier) and its blast radius is every instance it applies to.
A firewall doesn't reference instances by id — applicability is inferred in the graph
layer (same network + target tag/SA match), which is exactly the implicit coupling
Terraform itself doesn't track.
"""

from __future__ import annotations

from typing import Any

from statewatch.normalizer import (
    GCP_PROVIDER,
    Resource,
    firewall_id,
    network_ref,
    short_name,
)

RESOURCE_TYPE = "google_compute_firewall"


def _rules(raw: Any) -> list[dict[str, Any]]:
    """Normalize allow/deny blocks to sorted ``{protocol, ports}`` dicts."""
    out: list[dict[str, Any]] = []
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        proto = r.get("protocol") or r.get("IPProtocol")
        ports = sorted(str(p) for p in (r.get("ports") or []))
        out.append({"protocol": proto, "ports": ports})
    return sorted(out, key=lambda d: (d.get("protocol") or ""))


def _attributes(
    *,
    network: str | None,
    direction: str | None,
    priority: Any,
    disabled: bool | None,
    source_ranges: Any,
    destination_ranges: Any,
    target_tags: Any,
    target_service_accounts: Any,
    allowed: Any,
    denied: Any,
) -> dict[str, Any]:
    return {
        "network": short_name(network) if network else None,
        "direction": (direction or "INGRESS").upper(),
        "priority": int(priority) if priority is not None else 1000,
        "disabled": bool(disabled),
        "source_ranges": sorted(source_ranges or []),
        "destination_ranges": sorted(destination_ranges or []),
        "target_tags": sorted(target_tags or []),
        "target_service_accounts": sorted(target_service_accounts or []),
        "allowed": _rules(allowed),
        "denied": _rules(denied),
    }


def normalize_from_tfstate(
    attrs: dict[str, Any],
    *,
    project: str,
    terraform_address: str | None = None,
    depends_on: tuple[str, ...] = (),
) -> Resource:
    name = attrs.get("name", "")
    network = attrs.get("network")
    return Resource(
        resource_id=firewall_id(project, name),
        resource_type=RESOURCE_TYPE,
        provider=GCP_PROVIDER,
        name=name,
        parent_refs=(network_ref(project, network),) if network else (),
        attributes=_attributes(
            network=network,
            direction=attrs.get("direction"),
            priority=attrs.get("priority"),
            disabled=attrs.get("disabled"),
            source_ranges=attrs.get("source_ranges"),
            destination_ranges=attrs.get("destination_ranges"),
            target_tags=attrs.get("target_tags"),
            target_service_accounts=attrs.get("target_service_accounts"),
            allowed=attrs.get("allow"),
            denied=attrs.get("deny"),
        ),
        source="terraform",
        terraform_address=terraform_address,
        depends_on=tuple(depends_on),
    )


def normalize_from_cai(asset: dict[str, Any], *, project: str) -> Resource:
    data = (asset.get("resource") or {}).get("data") or {}
    name = data.get("name", "")
    network = data.get("network")
    return Resource(
        resource_id=firewall_id(project, name),
        resource_type=RESOURCE_TYPE,
        provider=GCP_PROVIDER,
        name=name,
        parent_refs=(network_ref(project, network),) if network else (),
        attributes=_attributes(
            network=network,
            direction=data.get("direction"),
            priority=data.get("priority"),
            disabled=data.get("disabled"),
            source_ranges=data.get("sourceRanges"),
            destination_ranges=data.get("destinationRanges"),
            target_tags=data.get("targetTags"),
            target_service_accounts=data.get("targetServiceAccounts"),
            allowed=data.get("allowed"),
            denied=data.get("denied"),
        ),
        source="live",
    )
