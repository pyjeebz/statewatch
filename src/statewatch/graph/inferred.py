"""Inferred dependency edges from resource attributes.

This is the most defensible part of statewatch: dependencies Terraform itself does not
track. When a compute instance has ``subnetwork = projects/x/regions/y/subnetworks/z`` in
an attribute, that is a real dependency on that subnet whether or not ``depends_on`` ever
mentions it.

Phase 1's normalizer already did the extraction work — it populated
:attr:`statewatch.normalizer.Resource.parent_refs` with canonical cloud ids for the
network, subnetwork and service-account references. So inference here is "read
``parent_refs`` and classify each target", **not** re-parsing attribute strings. Keeping
the string-parsing in the normalizer (per-resource, pure) and classification here keeps
the seam clean for Phase 3, when more resource types add more parent_refs.
"""

from __future__ import annotations

from dataclasses import dataclass

from statewatch.normalizer import Resource


@dataclass(frozen=True)
class InferredEdge:
    """An inferred dependency: ``source_id`` depends on ``target_id``.

    ``target_type``/``target_name`` describe the referenced resource so the builder can
    create a sensible (usually external, in Phase 1) placeholder node. ``source_attribute``
    is the instance attribute the dependency was inferred from.
    """

    source_id: str
    target_id: str
    target_type: str
    target_name: str
    source_attribute: str
    reason: str


def _classify_ref(ref: str) -> tuple[str, str, str]:
    """Map a canonical parent-ref id to ``(resource_type, name, source_attribute)``.

    Recognizes the GCP id shapes Phase 1's normalizer emits. Anything unrecognized is
    still surfaced (as an ``unknown`` type) rather than dropped — a dependency we can't
    classify is still a dependency, and hiding it would defeat the point.
    """
    last = ref.rstrip("/").rsplit("/", 1)[-1]
    if "/serviceAccounts/" in ref:
        return "google_service_account", last, "service_account"
    if "/global/networks/" in ref:
        return "google_compute_network", last, "network"
    if "/subnetworks/" in ref:
        return "google_compute_subnetwork", last, "subnetwork"
    return "unknown", last, "attribute"


def inferred_edges(resource: Resource) -> list[InferredEdge]:
    """Return the inferred dependency edges for one normalized resource.

    Self-references are dropped (a resource cannot depend on itself).
    """
    edges: list[InferredEdge] = []
    for ref in resource.parent_refs:
        if ref == resource.resource_id:
            continue
        rtype, rname, attr = _classify_ref(ref)
        edges.append(
            InferredEdge(
                source_id=resource.resource_id,
                target_id=ref,
                target_type=rtype,
                target_name=rname,
                source_attribute=attr,
                reason=f"inferred from {resource.resource_type} {attr} attribute",
            )
        )
    return edges


def _firewall_applies(fw: Resource, inst: Resource) -> bool:
    """Does ``fw`` govern ``inst``? Same network + INGRESS + target tag/SA match.

    A firewall never references instances by id — applicability is implicit (network +
    target tags / service accounts). Modelling it is exactly the coupling Terraform
    doesn't track, and it's what makes firewall drift show a real instance blast radius.
    Heuristic, not a packet-level model — stated honestly.
    """
    fa, ia = fw.attributes, inst.attributes
    if fa.get("direction", "INGRESS") != "INGRESS" or fa.get("disabled"):
        return False
    if not fa.get("network") or fa.get("network") != ia.get("network"):
        return False
    target_tags = set(fa.get("target_tags") or [])
    target_sas = set(fa.get("target_service_accounts") or [])
    if not target_tags and not target_sas:
        return True  # untargeted firewall applies to every instance in the network
    if target_tags & set(ia.get("tags") or []):
        return True
    return bool(target_sas and ia.get("service_account") in target_sas)


def firewall_applicability_edges(resources: list[Resource]) -> list[InferredEdge]:
    """Cross-resource inference: ``instance -> firewall`` when the firewall applies.

    Edge direction follows the convention (instance *depends on* the firewall protecting
    it), so a firewall drift surfaces its governed instances as predecessors.
    """
    firewalls = [r for r in resources if r.resource_type == "google_compute_firewall"]
    instances = [r for r in resources if r.resource_type == "google_compute_instance"]
    edges: list[InferredEdge] = []
    for fw in firewalls:
        for inst in instances:
            if _firewall_applies(fw, inst):
                edges.append(
                    InferredEdge(
                        source_id=inst.resource_id,
                        target_id=fw.resource_id,
                        target_type=fw.resource_type,
                        target_name=fw.name,
                        source_attribute="firewall",
                        reason="inferred firewall applicability (network + target match)",
                    )
                )
    return edges
