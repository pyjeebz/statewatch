"""Severity classifier — how bad is the drift itself?

Independent of impact (that's the graph's job). Maps each :class:`~statewatch.differ.Change`
to CRITICAL / MEDIUM / LOW per the SPEC rules, and a whole
:class:`~statewatch.differ.ResourceDiff` to the max severity across its changes.

Unrecognized changes default to **MEDIUM** — a config change we haven't categorized is
worth a look but shouldn't cry wolf as CRITICAL. Deliberate, documented default.
"""

from __future__ import annotations

from statewatch.differ import ResourceDiff

CRITICAL = "CRITICAL"
MEDIUM = "MEDIUM"
LOW = "LOW"
NONE = "NONE"

# Higher index = worse. Used to take the max severity across changes.
_ORDER = [NONE, LOW, MEDIUM, CRITICAL]


def severity_rank(sev: str) -> int:
    return _ORDER.index(sev) if sev in _ORDER else _ORDER.index(MEDIUM)


def max_severity(a: str, b: str) -> str:
    return a if severity_rank(a) >= severity_rank(b) else b


def _is_truthy(v: object) -> bool:
    return v not in (None, "", [], {}, False)


def _instance_change_severity(path: str, old: object, new: object) -> str:
    if path == "external_ip" and not _is_truthy(old) and _is_truthy(new):
        return CRITICAL  # public IP exposure on a previously-private instance
    head = path.split(".", 1)[0].split("[", 1)[0]
    if head in ("labels", "tags"):
        return LOW
    return MEDIUM  # machine_type, metadata, service_account, scopes, … and unknowns


def _gke_change_severity(path: str, old: object, new: object) -> str:
    # Private cluster -> public node IPs is an exposure event, same class as a public IP
    # on a previously-private instance.
    if path == "private_nodes" and old is True and new is False:
        return CRITICAL
    return MEDIUM  # node config / version / channel / network changes


def _subnetwork_change_severity(path: str) -> str:
    head = path.split(".", 1)[0].split("[", 1)[0]
    if head in ("private_ip_google_access",):
        return LOW
    return MEDIUM  # ip_cidr_range, secondary_ip_range, purpose, … and unknowns


def classify_resource_diff(diff: ResourceDiff) -> tuple[str, dict[str, str]]:
    """Return ``(overall_severity, {change_path: severity})`` for one resource's drift.

    Status-only drift (a resource present on just one side) is classified too: a
    missing/rogue firewall is CRITICAL; anything else missing/unmanaged is MEDIUM.
    """
    rtype = diff.resource_type

    if diff.status in ("missing_in_live", "unmanaged"):
        sev = CRITICAL if rtype == "google_compute_firewall" else MEDIUM
        return sev, {}

    per_path: dict[str, str] = {}
    overall = NONE
    for ch in diff.changes:
        if rtype == "google_compute_firewall":
            # Firewall added/removed/modified outside Terraform is always CRITICAL.
            sev = CRITICAL
        elif rtype == "google_compute_subnetwork":
            sev = _subnetwork_change_severity(ch.path)
        elif rtype == "google_container_cluster":
            sev = _gke_change_severity(ch.path, ch.old_value, ch.new_value)
        elif rtype == "google_compute_instance":
            sev = _instance_change_severity(ch.path, ch.old_value, ch.new_value)
        else:
            sev = MEDIUM
        per_path[ch.path] = sev
        overall = max_severity(overall, sev)
    return overall, per_path
