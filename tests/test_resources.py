from __future__ import annotations

from typing import Any

from statewatch.resources import normalize_cai, normalize_tf
from statewatch.resources.compute_instance import normalize_from_tfstate as inst_tf
from statewatch.resources.firewall import normalize_from_tfstate as fw_tf
from statewatch.resources.subnetwork import normalize_from_cai as sub_cai
from statewatch.resources.subnetwork import normalize_from_tfstate as sub_tf

PROJECT = "demo-project"


def test_firewall_normalization() -> None:
    r = fw_tf(
        {
            "name": "allow-http",
            "network": "https://www.googleapis.com/compute/v1/projects/demo-project/global/networks/prod-vpc",
            "direction": "ingress",
            "priority": 1000,
            "source_ranges": ["10.0.0.0/8", "10.1.0.0/16"],
            "target_tags": ["http-server"],
            "allow": [{"protocol": "tcp", "ports": ["443", "80"]}],
        },
        project=PROJECT,
    )
    assert r.resource_id == "projects/demo-project/global/firewalls/allow-http"
    assert r.resource_type == "google_compute_firewall"
    assert r.parent_refs == ("projects/demo-project/global/networks/prod-vpc",)
    assert r.attributes["direction"] == "INGRESS"
    assert r.attributes["source_ranges"] == ["10.0.0.0/8", "10.1.0.0/16"]  # sorted
    assert r.attributes["allowed"] == [{"protocol": "tcp", "ports": ["443", "80"]}]


def test_subnetwork_id_is_region_qualified_and_source_agnostic() -> None:
    tf = sub_tf(
        {
            "name": "prod-subnet",
            "region": "https://www.googleapis.com/compute/v1/projects/demo-project/regions/us-central1",
            "network": "https://www.googleapis.com/compute/v1/projects/demo-project/global/networks/prod-vpc",
            "ip_cidr_range": "10.0.0.0/24",
        },
        project=PROJECT,
    )
    cai = sub_cai(
        {
            "asset_type": "compute.googleapis.com/Subnetwork",
            "resource": {
                "data": {
                    "name": "prod-subnet",
                    "region": "https://www.googleapis.com/compute/v1/projects/demo-project/regions/us-central1",
                    "network": "https://www.googleapis.com/compute/v1/projects/demo-project/global/networks/prod-vpc",
                    "ipCidrRange": "10.0.0.0/24",
                }
            },
        },
        project=PROJECT,
    )
    assert tf.resource_id == "projects/demo-project/regions/us-central1/subnetworks/prod-subnet"
    assert tf.resource_id == cai.resource_id
    assert tf.attributes == cai.attributes  # identical config -> no drift


def test_known_issue_1_subnet_ref_unifies_across_input_forms() -> None:
    """An instance's inferred subnet ref must equal the subnet resource's id, for
    bare-name, path, and full-URL subnetwork attribute forms (KNOWN_ISSUES #1)."""
    subnet = sub_tf(
        {"name": "prod-subnet", "region": "us-central1", "network": "prod-vpc"},
        project=PROJECT,
    )
    base: dict[str, Any] = {
        "name": "vm",
        "zone": "us-central1-a",  # region us-central1 derived from zone
        "machine_type": "e2-small",
        "network_interface": [{"network": "prod-vpc", "subnetwork": None}],
    }
    forms = [
        "prod-subnet",  # bare name -> region from instance zone
        "projects/demo-project/regions/us-central1/subnetworks/prod-subnet",
        "https://www.googleapis.com/compute/v1/projects/demo-project/regions/us-central1/subnetworks/prod-subnet",
    ]
    for form in forms:
        attrs = {**base, "network_interface": [{"network": "prod-vpc", "subnetwork": form}]}
        inst = inst_tf(attrs, project=PROJECT)
        assert subnet.resource_id in inst.parent_refs, f"mismatch for form: {form}"


def test_registry_dispatch_and_unknown_types() -> None:
    assert normalize_tf("google_storage_bucket", {"name": "b"}, project=PROJECT) is None
    r = normalize_tf(
        "google_compute_subnetwork",
        {"name": "s", "region": "us-central1", "network": "n"},
        project=PROJECT,
    )
    assert r is not None and r.resource_type == "google_compute_subnetwork"
    assert normalize_cai({"asset_type": "compute.googleapis.com/Unknown"}, project=PROJECT) is None
