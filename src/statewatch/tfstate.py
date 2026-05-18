"""Terraform state loader.

Parses a ``.tfstate`` JSON file and extracts the resource instances statewatch cares
about. Phase 1 extracts only ``google_compute_instance``; later phases extend this with
firewall rules, subnetworks and GKE clusters.

Normalization into the common :class:`statewatch.normalizer.Resource` shape happens in
``normalizer.py`` — this module returns raw attribute dicts plus enough provenance
(module address, index key) to identify each instance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Terraform state format versions we know how to read. v4 has been current since
# Terraform 0.13 and is what every modern setup emits.
_SUPPORTED_STATE_VERSIONS = frozenset({4})


class TerraformStateError(ValueError):
    """Raised when a file is not a recognizable / supported Terraform state."""


@dataclass(frozen=True)
class TerraformResourceInstance:
    """One instance of a managed resource from ``.tfstate``.

    A single ``resource`` block in state can expand to multiple instances via ``count`` or
    ``for_each``; each gets its own entry here. ``module`` is the module address (empty
    string for the root module) and ``index_key`` is the count index / for_each key, or
    ``None`` for a singleton.
    """

    type: str
    name: str
    module: str
    index_key: Any
    attributes: dict[str, Any]
    #: Explicit ``depends_on`` / reference targets recorded in state, as Terraform
    #: addresses (e.g. ``google_compute_subnetwork.prod_subnet``). Phase 2 turns these
    #: into automatic graph edges. Empty when the instance declares no dependencies.
    dependencies: tuple[str, ...] = ()

    @property
    def address(self) -> str:
        """Terraform-style address, e.g. ``module.web.google_compute_instance.api[0]``."""
        base = f"{self.type}.{self.name}"
        if self.module:
            base = f"{self.module}.{base}"
        if self.index_key is not None:
            key = repr(self.index_key) if isinstance(self.index_key, str) else self.index_key
            base = f"{base}[{key}]"
        return base


def _read_gcs(uri: str) -> str:
    """Download ``gs://bucket/path`` text using Application Default Credentials.

    Reuses ADC (the same auth as the Cloud Asset Inventory client) — no separate
    credential path.
    """
    try:
        from google.cloud import storage  # type: ignore[attr-defined]
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise TerraformStateError(
            "google-cloud-storage is required to read gs:// state; install statewatch "
            "with its dependencies."
        ) from exc
    without_scheme = uri[len("gs://") :]
    bucket_name, _, blob_path = without_scheme.partition("/")
    if not bucket_name or not blob_path:
        raise TerraformStateError(f"{uri}: expected gs://<bucket>/<path>")
    try:
        client = storage.Client()
        blob = client.bucket(bucket_name).blob(blob_path)
        return blob.download_as_text()
    except Exception as exc:  # auth / not-found / permission -> uniform error
        raise TerraformStateError(f"{uri}: could not read GCS object: {exc}") from exc


def load_tfstate(path: str | Path) -> dict[str, Any]:
    """Read and parse a ``.tfstate`` from a local path or a ``gs://`` URI.

    Raises:
        FileNotFoundError: if a local ``path`` does not exist.
        TerraformStateError: if the file is unreadable, not valid JSON, or not a
            supported state format version.
    """
    src = str(path)
    if src.startswith("gs://"):
        raw = _read_gcs(src)
    else:
        p = Path(path)
        try:
            raw = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TerraformStateError(f"{src}: not valid JSON: {exc}") from exc
    if not isinstance(state, dict):
        raise TerraformStateError(f"{src}: expected a JSON object at the top level")
    version = state.get("version")
    if version not in _SUPPORTED_STATE_VERSIONS:
        raise TerraformStateError(
            f"{src}: unsupported Terraform state version {version!r} "
            f"(supported: {sorted(_SUPPORTED_STATE_VERSIONS)})"
        )
    return state


def iter_managed_resources(
    state: dict[str, Any], *, type_filter: str | None = None
) -> list[TerraformResourceInstance]:
    """Flatten every managed resource instance in ``state``, optionally filtered by type.

    Data sources (``mode == "data"``) are skipped — only ``mode == "managed"`` resources
    represent infrastructure that can drift.
    """
    out: list[TerraformResourceInstance] = []
    for resource in state.get("resources", []) or []:
        if resource.get("mode") != "managed":
            continue
        rtype = resource.get("type", "")
        if type_filter is not None and rtype != type_filter:
            continue
        module = resource.get("module", "")
        rname = resource.get("name", "")
        for inst in resource.get("instances", []) or []:
            attrs = inst.get("attributes")
            if not isinstance(attrs, dict):
                continue
            out.append(
                TerraformResourceInstance(
                    type=rtype,
                    name=rname,
                    module=module,
                    index_key=inst.get("index_key"),
                    attributes=attrs,
                    dependencies=tuple(inst.get("dependencies") or ()),
                )
            )
    return out


def extract_compute_instances(state: dict[str, Any]) -> list[TerraformResourceInstance]:
    """Return every ``google_compute_instance`` instance in the parsed state."""
    return iter_managed_resources(state, type_filter="google_compute_instance")
