from __future__ import annotations

from statewatch.classifier import (
    CRITICAL,
    LOW,
    MEDIUM,
    NONE,
    classify_resource_diff,
    max_severity,
    severity_rank,
)
from statewatch.differ import Change, ResourceDiff


def _diff(rtype: str, changes: list[Change], status: str = "drifted") -> ResourceDiff:
    return ResourceDiff(
        resource_id="rid", resource_type=rtype, name="n", status=status, changes=changes
    )


def _c(path: str, old: object, new: object) -> Change:
    return Change("rid", path, old, new, "modified")


def test_severity_ordering() -> None:
    ranks = [severity_rank(s) for s in (CRITICAL, MEDIUM, LOW, NONE)]
    assert ranks == sorted(ranks, reverse=True) and len(set(ranks)) == 4
    assert max_severity(LOW, CRITICAL) == CRITICAL
    assert max_severity(MEDIUM, LOW) == MEDIUM


def test_firewall_any_change_is_critical() -> None:
    sev, per = classify_resource_diff(
        _diff("google_compute_firewall", [_c("source_ranges[0]", "10.0.0.0/8", "0.0.0.0/0")])
    )
    assert sev == CRITICAL
    assert per["source_ranges[0]"] == CRITICAL


def test_instance_public_ip_exposure_is_critical() -> None:
    sev, _ = classify_resource_diff(
        _diff("google_compute_instance", [_c("external_ip", None, "34.1.2.3")])
    )
    assert sev == CRITICAL


def test_instance_machine_type_medium_labels_low() -> None:
    sev_m, _ = classify_resource_diff(
        _diff("google_compute_instance", [_c("machine_type", "e2-small", "n2-standard-4")])
    )
    sev_l, _ = classify_resource_diff(
        _diff("google_compute_instance", [_c("labels.team", "a", "b")])
    )
    assert sev_m == MEDIUM
    assert sev_l == LOW


def test_subnetwork_cidr_medium_pga_low() -> None:
    sev_c, _ = classify_resource_diff(
        _diff("google_compute_subnetwork", [_c("ip_cidr_range", "10.0.0.0/24", "10.0.0.0/20")])
    )
    sev_p, _ = classify_resource_diff(
        _diff("google_compute_subnetwork", [_c("private_ip_google_access", True, False)])
    )
    assert sev_c == MEDIUM
    assert sev_p == LOW


def test_status_only_drift_severity() -> None:
    fw_missing = classify_resource_diff(_diff("google_compute_firewall", [], "missing_in_live"))
    inst_unmanaged = classify_resource_diff(
        _diff("google_compute_instance", [], "unmanaged")
    )
    assert fw_missing[0] == CRITICAL  # a removed/rogue firewall is security-critical
    assert inst_unmanaged[0] == MEDIUM


def test_overall_is_max_across_changes() -> None:
    sev, _ = classify_resource_diff(
        _diff(
            "google_compute_instance",
            [_c("labels.x", "1", "2"), _c("external_ip", None, "1.2.3.4")],
        )
    )
    assert sev == CRITICAL
