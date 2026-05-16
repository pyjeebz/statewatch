from __future__ import annotations

from statewatch.differ import ADDED, MODIFIED, REMOVED, diff_resource, diff_resources
from statewatch.normalizer import Resource


def _resource(rid: str, attrs: dict, *, source: str = "terraform", name: str = "vm") -> Resource:
    return Resource(
        resource_id=rid,
        resource_type="google_compute_instance",
        provider="gcp",
        name=name,
        attributes=attrs,
        source=source,
    )


def test_no_diff_between_identical_resources() -> None:
    attrs = {"machine_type": "e2-medium", "metadata": {"a": "1"}, "tags": ["x"]}
    a = _resource("rid-1", dict(attrs))
    b = _resource("rid-1", dict(attrs), source="live")
    assert diff_resource(a, b) == []


def test_scalar_modification_is_reported_with_path() -> None:
    a = _resource("rid-1", {"machine_type": "n2-standard-4", "tags": ["x"]})
    b = _resource("rid-1", {"machine_type": "n2-standard-8", "tags": ["x"]}, source="live")
    changes = diff_resource(a, b)
    assert len(changes) == 1
    ch = changes[0]
    assert ch.path == "machine_type"
    assert ch.old_value == "n2-standard-4"
    assert ch.new_value == "n2-standard-8"
    assert ch.change_kind == MODIFIED
    assert ch.resource_id == "rid-1"


def test_nested_added_and_removed_keys() -> None:
    a = _resource("rid-1", {"metadata": {"keep": "1", "gone": "2"}})
    b = _resource("rid-1", {"metadata": {"keep": "1", "new": "3"}}, source="live")
    changes = {(c.path, c.change_kind): c for c in diff_resource(a, b)}
    assert ("metadata.gone", REMOVED) in changes
    assert ("metadata.new", ADDED) in changes
    assert changes[("metadata.gone", REMOVED)].old_value == "2"
    assert changes[("metadata.new", ADDED)].new_value == "3"
    assert len(changes) == 2


def test_list_element_changes_use_index_segments() -> None:
    a = _resource("rid-1", {"scopes": ["compute", "storage"]})
    b = _resource("rid-1", {"scopes": ["compute", "storage", "logging"]}, source="live")
    changes = diff_resource(a, b)
    assert len(changes) == 1
    assert changes[0].path == "scopes[2]"
    assert changes[0].change_kind == ADDED
    assert changes[0].new_value == "logging"


def test_diff_resources_pairs_by_id_and_flags_one_sided() -> None:
    tf = [
        _resource("rid-drift", {"machine_type": "a"}, name="drifter"),
        _resource("rid-only-tf", {"machine_type": "x"}, name="ghost"),
        _resource("rid-same", {"machine_type": "z"}, name="stable"),
    ]
    live = [
        _resource("rid-drift", {"machine_type": "b"}, source="live", name="drifter"),
        _resource("rid-only-live", {"machine_type": "y"}, source="live", name="orphan"),
        _resource("rid-same", {"machine_type": "z"}, source="live", name="stable"),
    ]
    results = {r.resource_id: r for r in diff_resources(tf, live)}

    # Unchanged resource is omitted entirely.
    assert "rid-same" not in results

    assert results["rid-drift"].status == "drifted"
    assert results["rid-drift"].changes[0].path == "machine_type"

    assert results["rid-only-tf"].status == "missing_in_live"
    assert results["rid-only-tf"].changes == []

    assert results["rid-only-live"].status == "unmanaged"
