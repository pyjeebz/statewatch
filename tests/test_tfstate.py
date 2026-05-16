from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from statewatch.tfstate import (
    TerraformStateError,
    extract_compute_instances,
    iter_managed_resources,
    load_tfstate,
)


def test_load_tfstate_parses_and_validates_version(tfstate_path: Path) -> None:
    state = load_tfstate(tfstate_path)
    assert state["version"] == 4
    assert isinstance(state["resources"], list)


def test_load_tfstate_rejects_unsupported_version(tmp_path: Path) -> None:
    bad = tmp_path / "old.tfstate"
    bad.write_text(json.dumps({"version": 3, "resources": []}), encoding="utf-8")
    with pytest.raises(TerraformStateError, match="unsupported Terraform state version"):
        load_tfstate(bad)


def test_load_tfstate_rejects_non_json(tmp_path: Path) -> None:
    bad = tmp_path / "garbage.tfstate"
    bad.write_text("this is not json", encoding="utf-8")
    with pytest.raises(TerraformStateError, match="not valid JSON"):
        load_tfstate(bad)


def test_extract_compute_instances_finds_root_and_module_instances(tfstate: dict[str, Any]) -> None:
    instances = extract_compute_instances(tfstate)
    names = sorted(i.attributes["name"] for i in instances)
    assert names == ["api-server-prod", "worker-01"]

    by_name = {i.attributes["name"]: i for i in instances}
    # The worker lives inside module.data_plane and has a count index.
    worker = by_name["worker-01"]
    assert worker.module == "module.data_plane"
    assert worker.index_key == 0
    assert worker.address == "module.data_plane.google_compute_instance.worker[0]"
    # The root api server has no module / index.
    assert by_name["api-server-prod"].module == ""
    assert by_name["api-server-prod"].index_key is None


def test_extract_ignores_other_types_and_data_sources(tfstate: dict[str, Any]) -> None:
    # The fixture also contains a google_storage_bucket and a data source; neither should
    # appear when we ask for compute instances...
    compute = extract_compute_instances(tfstate)
    assert all(i.type == "google_compute_instance" for i in compute)
    # ...and the data source must never be returned even when iterating all managed types.
    all_managed = iter_managed_resources(tfstate)
    assert {i.type for i in all_managed} == {"google_compute_instance", "google_storage_bucket"}
