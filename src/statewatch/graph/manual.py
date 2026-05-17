"""Manual dependency edges declared in ``statewatch.yaml``.

Manual edges exist for coupling Terraform state cannot express: cross-project,
cross-account, or non-Terraform-managed dependencies. They are user-declared, so a
``reason`` is **required** — an undocumented manual edge is unmaintainable.

``from``/``to`` may be either a Terraform address (``google_compute_instance.api``) or a
raw cloud ``resource_id``. Resolution to a graph node happens in
:mod:`statewatch.graph.builder` (it owns the address→id index); this module only loads
and validates the declarations.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ManualEdgeError(ValueError):
    """Raised when ``statewatch.yaml`` manual edges are malformed."""


@dataclass(frozen=True)
class ManualEdge:
    """A user-declared dependency: ``from_ref`` depends on ``to_ref``.

    ``from_ref``/``to_ref`` are unresolved — a Terraform address or a cloud resource_id.
    Direction matches the rest of the graph: an edge ``from_ref → to_ref`` means
    "from_ref depends on to_ref" (if to_ref changes, from_ref may be affected).
    """

    from_ref: str
    to_ref: str
    reason: str


def manual_edges_from_dicts(items: list[dict[str, Any]]) -> list[ManualEdge]:
    """Build :class:`ManualEdge` objects from parsed config dicts.

    Each item must have non-empty ``from``, ``to`` and ``reason``. Raises
    :class:`ManualEdgeError` (never a bare KeyError) so the CLI can report it cleanly.
    """
    edges: list[ManualEdge] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ManualEdgeError(
                f"dependencies[{i}]: expected a mapping, got {type(item).__name__}"
            )
        src = item.get("from")
        dst = item.get("to")
        reason = item.get("reason")
        for field_name, value in (("from", src), ("to", dst), ("reason", reason)):
            if not isinstance(value, str) or not value.strip():
                raise ManualEdgeError(
                    f"dependencies[{i}]: '{field_name}' is required and must be a "
                    f"non-empty string (manual edges must document their reason)"
                )
        assert isinstance(src, str) and isinstance(dst, str) and isinstance(reason, str)
        edges.append(
            ManualEdge(from_ref=src.strip(), to_ref=dst.strip(), reason=reason.strip())
        )
    return edges


def load_manual_edges(path: str | Path) -> list[ManualEdge]:
    """Load manual edges from a ``statewatch.yaml``-shaped file.

    Reads the top-level ``dependencies:`` list. A missing or empty ``dependencies`` key is
    valid and yields no edges. Raises :class:`ManualEdgeError` on unreadable/invalid YAML.
    """
    import yaml

    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ManualEdgeError(f"{p}: invalid YAML: {exc}") from exc
    if data is None:
        return []
    if not isinstance(data, dict):
        raise ManualEdgeError(f"{p}: expected a YAML mapping at the top level")
    deps = data.get("dependencies") or []
    if not isinstance(deps, list):
        raise ManualEdgeError(f"{p}: 'dependencies' must be a list")
    return manual_edges_from_dicts(deps)
