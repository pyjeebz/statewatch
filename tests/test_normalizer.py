from __future__ import annotations

from typing import Any

from statewatch.normalizer import Resource
from statewatch.resources.compute_instance import (
    normalize_from_cai as normalize_compute_instance_from_cai,
)
from statewatch.resources.compute_instance import (
    normalize_from_tfstate as normalize_compute_instance_from_tfstate,
)
from statewatch.tfstate import extract_compute_instances

PROJECT = "demo-project"


def _api_server_attrs(tfstate: dict[str, Any]) -> dict[str, Any]:
    by_name = {i.attributes["name"]: i for i in extract_compute_instances(tfstate)}
    return by_name["api-server-prod"].attributes


def test_tfstate_normalization_shape_and_refs(tfstate: dict[str, Any]) -> None:
    r = normalize_compute_instance_from_tfstate(_api_server_attrs(tfstate), project=PROJECT)
    assert isinstance(r, Resource)
    assert r.resource_id == "projects/demo-project/zones/us-central1-a/instances/api-server-prod"
    assert r.resource_type == "google_compute_instance"
    assert r.provider == "gcp"
    assert r.name == "api-server-prod"
    assert r.source == "terraform"

    # Comparable attributes are flattened and short-named.
    assert r.attributes["machine_type"] == "n2-standard-4"
    assert r.attributes["network"] == "prod-vpc"
    assert r.attributes["subnetwork"] == "prod-subnet"
    assert r.attributes["metadata"] == {"enable-oslogin": "TRUE"}
    assert r.attributes["labels"] == {"env": "prod", "team": "platform"}
    assert r.attributes["tags"] == ["http-server", "ssh"]
    assert r.attributes["external_ip"] is None
    assert r.attributes["preemptible"] is False

    # parent_refs becomes Phase 2 graph edges: network, subnetwork, service account.
    assert "projects/demo-project/global/networks/prod-vpc" in r.parent_refs
    assert any("subnetworks/prod-subnet" in ref for ref in r.parent_refs)
    sa_ref = "projects/demo-project/serviceAccounts/sa-api@demo-project.iam.gserviceaccount.com"
    assert sa_ref in r.parent_refs


def test_tfstate_normalization_strips_provider_noise(tfstate: dict[str, Any]) -> None:
    r = normalize_compute_instance_from_tfstate(_api_server_attrs(tfstate), project=PROJECT)
    # None of the server-assigned / generated / Terraform-bookkeeping keys survive into
    # the comparable attribute bag.
    for noisy in (
        "self_link",
        "instance_id",
        "cpu_platform",
        "label_fingerprint",
        "metadata_fingerprint",
        "tags_fingerprint",
        "timeouts",
        "status",
        "current_status",
        "boot_disk",
        "project",
    ):
        assert noisy not in r.attributes


def test_cai_normalization_matches_tfstate_resource_id(tfstate: dict[str, Any]) -> None:
    tf = normalize_compute_instance_from_tfstate(_api_server_attrs(tfstate), project=PROJECT)

    cai_name = (
        "//compute.googleapis.com/projects/demo-project"
        "/zones/us-central1-a/instances/api-server-prod"
    )
    cai_asset = {
        "name": cai_name,
        "asset_type": "compute.googleapis.com/Instance",
        "resource": {
            "data": {
                "name": "api-server-prod",
                "zone": "https://www.googleapis.com/compute/v1/projects/demo-project/zones/us-central1-a",
                "machineType": "https://www.googleapis.com/compute/v1/projects/demo-project/zones/us-central1-a/machineTypes/n2-standard-4",
                "status": "RUNNING",
                "labels": {"env": "prod", "team": "platform"},
                "tags": {"items": ["http-server", "ssh"]},
                "metadata": {"items": [{"key": "enable-oslogin", "value": "TRUE"}]},
                "networkInterfaces": [
                    {
                        "network": "https://www.googleapis.com/compute/v1/projects/demo-project/global/networks/prod-vpc",
                        "subnetwork": "https://www.googleapis.com/compute/v1/projects/demo-project/regions/us-central1/subnetworks/prod-subnet",
                        "networkIP": "10.0.0.10",
                        "accessConfigs": [],
                    }
                ],
                "serviceAccounts": [
                    {"email": "sa-api@demo-project.iam.gserviceaccount.com", "scopes": ["https://www.googleapis.com/auth/cloud-platform"]}
                ],
                "scheduling": {"preemptible": False, "automaticRestart": True},
            }
        },
    }
    live = normalize_compute_instance_from_cai(cai_asset, project=PROJECT)

    assert live.resource_id == tf.resource_id
    assert live.source == "live"
    # With identical configuration the comparable attribute bags are equal -> no drift.
    assert live.attributes == tf.attributes
