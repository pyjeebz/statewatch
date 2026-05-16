"""Structural diff between normalized resources.

Given two :class:`statewatch.normalizer.Resource` objects representing the same resource
from different sources (Terraform-intended vs. live), produce a flat list of
:class:`Change` objects describing what differs.

This is intentionally dumb about *meaning* — severity classification and impact analysis
are separate concerns (Phase 3). The differ only answers "what changed".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from statewatch.normalizer import Resource

# change_kind values.
ADDED = "added"
REMOVED = "removed"
MODIFIED = "modified"


@dataclass(frozen=True)
class Change:
    """A single difference at one attribute path.

    Attributes:
        resource_id: The ``resource_id`` of the resource this change belongs to.
        path: Dotted path to the changed value within the resource's normalized
            attributes, e.g. ``machine_type`` or ``metadata.startup-script``. List
            elements use ``[index]`` segments, e.g. ``scopes[0]``.
        old_value: The value on the "old" side (Terraform). ``None`` for ``added``.
        new_value: The value on the "new" side (live). ``None`` for ``removed``.
        change_kind: One of ``"added"``, ``"removed"``, ``"modified"``.
    """

    resource_id: str
    path: str
    old_value: Any
    new_value: Any
    change_kind: str


def _join(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


def _diff_values(path: str, old: Any, new: Any) -> list[tuple[str, Any, Any, str]]:
    """Recursively diff two values, returning ``(path, old, new, kind)`` tuples."""
    if isinstance(old, dict) and isinstance(new, dict):
        changes: list[tuple[str, Any, Any, str]] = []
        for key in sorted(set(old) | set(new)):
            in_old, in_new = key in old, key in new
            if in_old and in_new:
                changes.extend(_diff_values(_join(path, key), old[key], new[key]))
            elif in_new:
                changes.append((_join(path, key), None, new[key], ADDED))
            else:
                changes.append((_join(path, key), old[key], None, REMOVED))
        return changes
    if isinstance(old, list) and isinstance(new, list):
        changes = []
        for i in range(max(len(old), len(new))):
            seg = f"{path}[{i}]"
            if i < len(old) and i < len(new):
                changes.extend(_diff_values(seg, old[i], new[i]))
            elif i < len(new):
                changes.append((seg, None, new[i], ADDED))
            else:
                changes.append((seg, old[i], None, REMOVED))
        return changes
    if old != new:
        return [(path, old, new, MODIFIED)]
    return []


def diff_resource(old: Resource, new: Resource) -> list[Change]:
    """Diff the normalized ``attributes`` of two resources representing the same thing.

    ``old`` is conventionally the Terraform side and ``new`` the live side, so a
    ``modified`` change reads as "Terraform says X, live is Y".
    """
    raw = _diff_values("", old.attributes, new.attributes)
    return [Change(new.resource_id, path, o, n, kind) for (path, o, n, kind) in raw]


@dataclass(frozen=True)
class ResourceDiff:
    """The drift outcome for one resource id."""

    resource_id: str
    resource_type: str
    name: str
    # "drifted": present on both sides with attribute differences.
    # "missing_in_live": in Terraform state but not found in live inventory.
    # "unmanaged": in live inventory but not in Terraform state.
    status: str
    changes: list[Change]


def diff_resources(old: list[Resource], new: list[Resource]) -> list[ResourceDiff]:
    """Pair resources by ``resource_id`` and diff each pair.

    Resources present on only one side are reported with status ``missing_in_live``
    (Terraform-only) or ``unmanaged`` (live-only) and no changes. Resources present on
    both sides with no attribute differences are omitted from the result.
    """
    old_by_id = {r.resource_id: r for r in old}
    new_by_id = {r.resource_id: r for r in new}
    results: list[ResourceDiff] = []

    for rid in sorted(set(old_by_id) | set(new_by_id)):
        o = old_by_id.get(rid)
        n = new_by_id.get(rid)
        if o is not None and n is not None:
            changes = diff_resource(o, n)
            if changes:
                results.append(
                    ResourceDiff(rid, n.resource_type, n.name, "drifted", changes)
                )
        elif o is not None:
            results.append(
                ResourceDiff(rid, o.resource_type, o.name, "missing_in_live", [])
            )
        else:
            assert n is not None
            results.append(ResourceDiff(rid, n.resource_type, n.name, "unmanaged", []))
    return results
