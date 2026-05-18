"""Minimal ``statewatch.yaml`` loader for notifications and watch settings.

Manual dependency edges are parsed separately by :mod:`statewatch.graph.manual`; this
module handles the rest of the schema that Phase 4 needs. ``${VAR}`` values are expanded
from the environment (the spec uses ``${SLACK_WEBHOOK_URL}``).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ENV = re.compile(r"\$\{([A-Z0-9_]+)\}")


class ConfigError(ValueError):
    """Raised when statewatch.yaml is malformed."""


@dataclass(frozen=True)
class SlackConfig:
    webhook: str
    severity_threshold: str = "MEDIUM"  # LOW | MEDIUM | CRITICAL
    include_impact_summary: bool = True


@dataclass(frozen=True)
class Config:
    project: str | None = None
    slack: SlackConfig | None = None
    watch_interval: str | None = None


def _expand(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return _ENV.sub(lambda m: os.environ.get(m.group(1), ""), value)


def load_config(path: str | Path) -> Config:
    """Parse a ``statewatch.yaml``. Missing optional sections are simply absent."""
    import yaml

    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except yaml.YAMLError as exc:
        raise ConfigError(f"{p}: invalid YAML: {exc}") from exc
    if data is None:
        return Config()
    if not isinstance(data, dict):
        raise ConfigError(f"{p}: expected a YAML mapping at the top level")

    slack_cfg = None
    slack = ((data.get("notifications") or {}).get("slack")) or {}
    if slack:
        webhook = _expand(slack.get("webhook") or "").strip()
        if webhook:
            slack_cfg = SlackConfig(
                webhook=webhook,
                severity_threshold=str(slack.get("severity_threshold", "medium")).upper(),
                include_impact_summary=bool(slack.get("include_impact_summary", True)),
            )

    watch = (data.get("watch") or {}).get("interval")

    return Config(
        project=data.get("project"),
        slack=slack_cfg,
        watch_interval=str(watch) if watch else None,
    )
