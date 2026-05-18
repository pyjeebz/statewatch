"""``google_compute_subnetwork`` normalization.

The widest-blast-radius type: every instance in a subnet depends on it, so a CIDR or
secondary-range change lights up the whole subnet. Its ``resource_id`` is region-qualified
and built from the shared :func:`statewatch.normalizer.subnetwork_id` so it unifies with
the subnet ref a compute instance derives (KNOWN_ISSUES #1).
"""

from __future__ import annotations

from typing import Any

from statewatch.normalizer import (
    GCP_PROVIDER,
    Resource,
    network_ref,
    short_name,
    subnetwork_id,
)

RESOURCE_TYPE = "google_compute_subnetwork"


def _secondary_ranges(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        name = r.get("range_name") or r.get("rangeName")
        cidr = r.get("ip_cidr_range") or r.get("ipCidrRange")
        out.append({"range_name": name, "ip_cidr_range": cidr})
    return sorted(out, key=lambda d: (d.get("range_name") or ""))


def _attributes(
    *,
    network: str | None,
    region: str | None,
    ip_cidr_range: str | None,
    secondary: Any,
    private_ip_google_access: bool | None,
    purpose: str | None,
) -> dict[str, Any]:
    return {
        "network": short_name(network) if network else None,
        "region": short_name(region) if region else None,
        "ip_cidr_range": ip_cidr_range,
        "secondary_ip_range": _secondary_ranges(secondary),
        "private_ip_google_access": bool(private_ip_google_access),
        "purpose": purpose,
    }


def normalize_from_tfstate(
    attrs: dict[str, Any],
    *,
    project: str,
    terraform_address: str | None = None,
    depends_on: tuple[str, ...] = (),
) -> Resource:
    name = attrs.get("name", "")
    region = attrs.get("region")
    network = attrs.get("network")
    return Resource(
        resource_id=subnetwork_id(project, short_name(region) or "", name),
        resource_type=RESOURCE_TYPE,
        provider=GCP_PROVIDER,
        name=name,
        parent_refs=(network_ref(project, network),) if network else (),
        attributes=_attributes(
            network=network,
            region=region,
            ip_cidr_range=attrs.get("ip_cidr_range"),
            secondary=attrs.get("secondary_ip_range"),
            private_ip_google_access=attrs.get("private_ip_google_access"),
            purpose=attrs.get("purpose"),
        ),
        source="terraform",
        terraform_address=terraform_address,
        depends_on=tuple(depends_on),
    )


def normalize_from_cai(asset: dict[str, Any], *, project: str) -> Resource:
    data = (asset.get("resource") or {}).get("data") or {}
    name = data.get("name", "")
    region = data.get("region", "")
    network = data.get("network")
    return Resource(
        resource_id=subnetwork_id(project, short_name(region) or "", name),
        resource_type=RESOURCE_TYPE,
        provider=GCP_PROVIDER,
        name=name,
        parent_refs=(network_ref(project, network),) if network else (),
        attributes=_attributes(
            network=network,
            region=region,
            ip_cidr_range=data.get("ipCidrRange"),
            secondary=data.get("secondaryIpRanges"),
            private_ip_google_access=data.get("privateIpGoogleAccess"),
            purpose=data.get("purpose"),
        ),
        source="live",
    )
