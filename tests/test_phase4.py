from __future__ import annotations

from pathlib import Path

import pytest

from statewatch import watchstate
from statewatch.classifier import CRITICAL, classify_resource_diff
from statewatch.cli import _parse_interval
from statewatch.config import load_config
from statewatch.differ import Change, ResourceDiff
from statewatch.notifiers.slack import SlackConfig, _blocks
from statewatch.report import Finding, Report
from statewatch.resources import normalize_tf
from statewatch.resources.gke_cluster import normalize_from_cai, normalize_from_tfstate
from statewatch.tfstate import TerraformStateError, load_tfstate

PROJECT = "demo-project"


# --- GKE (4th resource type) --------------------------------------------------------
def test_gke_normalization_tf_and_cai_unify() -> None:
    tf = normalize_from_tfstate(
        {
            "name": "prod-gke",
            "location": "us-central1",
            "network": "prod-vpc",
            "subnetwork": "prod-subnet",
            "node_config": [{"service_account": "gke-sa@demo-project.iam.gserviceaccount.com",
                             "machine_type": "e2-standard-4"}],
            "private_cluster_config": [{"enable_private_nodes": True}],
            "release_channel": [{"channel": "REGULAR"}],
        },
        project=PROJECT,
    )
    cai = normalize_from_cai(
        {
            "asset_type": "container.googleapis.com/Cluster",
            "resource": {"data": {
                "name": "prod-gke", "location": "us-central1",
                "network": "prod-vpc", "subnetwork": "prod-subnet",
                "nodeConfig": {"serviceAccount": "gke-sa@demo-project.iam.gserviceaccount.com",
                               "machineType": "e2-standard-4"},
                "privateClusterConfig": {"enablePrivateNodes": True},
                "releaseChannel": {"channel": "REGULAR"},
            }},
        },
        project=PROJECT,
    )
    assert tf.resource_id == "projects/demo-project/locations/us-central1/clusters/prod-gke"
    assert tf.resource_id == cai.resource_id
    assert tf.attributes == cai.attributes
    # parent_refs: network + region-qualified subnet + node SA
    assert "projects/demo-project/global/networks/prod-vpc" in tf.parent_refs
    assert any("subnetworks/prod-subnet" in r for r in tf.parent_refs)


def test_gke_registry_and_private_node_exposure_is_critical() -> None:
    assert normalize_tf("google_container_cluster", {"name": "c", "location": "us-east1"},
                        project=PROJECT) is not None
    diff = ResourceDiff(
        "rid", "google_container_cluster", "c", "drifted",
        [Change("rid", "private_nodes", True, False, "modified")],
    )
    sev, _ = classify_resource_diff(diff)
    assert sev == CRITICAL


# --- GCS backend (offline error paths; no network) ----------------------------------
def test_gcs_uri_validation() -> None:
    with pytest.raises(TerraformStateError, match="gs://<bucket>/<path>"):
        load_tfstate("gs://only-bucket")


def test_local_path_still_works(tmp_path: Path) -> None:
    p = tmp_path / "s.tfstate"
    p.write_text('{"version": 4, "resources": []}', encoding="utf-8")
    assert load_tfstate(str(p))["version"] == 4


# --- config.py ----------------------------------------------------------------------
def test_config_env_substitution_and_slack(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/abc")
    cfg_file = tmp_path / "statewatch.yaml"
    cfg_file.write_text(
        "project: p\n"
        "notifications:\n  slack:\n    webhook: ${SLACK_WEBHOOK_URL}\n"
        "    severity_threshold: critical\n"
        "watch:\n  interval: 15m\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    assert cfg.slack is not None
    assert cfg.slack.webhook == "https://hooks.example/abc"
    assert cfg.slack.severity_threshold == "CRITICAL"
    assert cfg.watch_interval == "15m"


# --- Slack block building (no network) ----------------------------------------------
def _finding(sev: str) -> Finding:
    return Finding("rid", "google_compute_firewall", "fw", "drifted", sev)


def test_slack_threshold_filters_findings() -> None:
    report = Report(findings=[_finding("LOW"), _finding("CRITICAL")])
    cfg = SlackConfig(webhook="x", severity_threshold="CRITICAL")
    blocks = _blocks(report, cfg)
    # header + the one CRITICAL finding only
    assert blocks and len(blocks) == 2
    assert "CRITICAL" in blocks[1]["text"]["text"]


def test_slack_returns_no_blocks_below_threshold() -> None:
    report = Report(findings=[_finding("LOW")])
    assert _blocks(report, SlackConfig(webhook="x", severity_threshold="MEDIUM")) == []


# --- watch state --------------------------------------------------------------------
def test_watch_fingerprint_and_changed_detection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STATEWATCH_STATE_DIR", str(tmp_path))
    report = Report(findings=[_finding("CRITICAL")])
    key = watchstate.state_key("p", "s.tfstate")

    # First time: everything is new.
    assert len(watchstate.changed_findings(report, key)) == 1
    watchstate.save(report, key)
    # Unchanged: nothing new.
    assert watchstate.changed_findings(report, key) == []
    # Severity escalates -> changed again.
    bumped = Report(findings=[_finding("MEDIUM")])
    assert len(watchstate.changed_findings(bumped, key)) == 1


def test_watch_notifies_only_changed_not_whole_report(tmp_path: Path, monkeypatch) -> None:
    """Regression: when one of several findings changes, --watch must notify on only
    that finding, not re-surface the unchanged ones."""
    monkeypatch.setenv("STATEWATCH_STATE_DIR", str(tmp_path))
    key = watchstate.state_key("p", "s.tfstate")

    stable = Finding("fw-stable", "google_compute_firewall", "stable", "drifted", "CRITICAL")
    v1 = Finding("sn", "google_compute_subnetwork", "sn", "drifted", "MEDIUM")
    watchstate.save(Report(findings=[stable, v1]), key)

    # Same scan except `sn` escalated; `stable` is byte-identical.
    v2 = Finding("sn", "google_compute_subnetwork", "sn", "drifted", "CRITICAL")
    full = Report(findings=[stable, v2])
    changed = watchstate.changed_findings(full, key)

    assert [f.resource_id for f in changed] == ["sn"]  # only the changed one
    notify = Report(findings=changed)
    assert all(f.resource_id != "fw-stable" for f in notify.findings)


# --- interval parsing ---------------------------------------------------------------
def test_parse_interval() -> None:
    assert _parse_interval("30s") == 30
    assert _parse_interval("5m") == 300
    assert _parse_interval("2h") == 7200
    for bad in ("", "30", "5x", "-1m", "0s"):
        with pytest.raises(ValueError):
            _parse_interval(bad)
