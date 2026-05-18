"""Persisted scan state for ``--watch`` — notify only on *new or changed* drift.

A small JSON file at ``~/.statewatch/state.json`` (override with ``STATEWATCH_STATE_DIR``)
keyed by ``project::tfstate-source`` so different configs/backends don't clobber each
other. Per resource we store a stable fingerprint of its finding; a finding is "new or
changed" when its fingerprint is absent or differs from the stored one.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from statewatch.report import Finding, Report

_SCHEMA = 1


def _state_path() -> Path:
    base = os.environ.get("STATEWATCH_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".statewatch"
    )
    return Path(base) / "state.json"


def state_key(project: str, tfstate: str) -> str:
    return f"{project}::{tfstate}"


def fingerprint(f: Finding) -> str:
    """Stable hash of what we'd notify about: severity + change paths + impact labels."""
    payload = json.dumps(
        {
            "severity": f.severity,
            "status": f.status,
            "changes": sorted(c.path for c in f.changes),
            "impact": sorted(f"{n.resource_id}:{n.label}" for n in f.impacted),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _load() -> dict:
    p = _state_path()
    if not p.exists():
        return {"schema": _SCHEMA, "keys": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("schema") != _SCHEMA:
            return {"schema": _SCHEMA, "keys": {}}
        return data
    except (json.JSONDecodeError, OSError):
        return {"schema": _SCHEMA, "keys": {}}


def changed_findings(report: Report, key: str) -> list[Finding]:
    """Return findings whose fingerprint is new or differs from the saved state."""
    prev = _load()["keys"].get(key, {})
    out = []
    for f in report.findings:
        if prev.get(f.resource_id) != fingerprint(f):
            out.append(f)
    return out


def save(report: Report, key: str) -> None:
    """Persist the current findings' fingerprints for ``key``."""
    data = _load()
    data["keys"][key] = {f.resource_id: fingerprint(f) for f in report.findings}
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
