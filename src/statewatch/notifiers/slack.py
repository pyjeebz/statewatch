"""Slack webhook notifier.

Off unless a webhook is configured (``statewatch.yaml`` ``notifications.slack``). Posts
findings at or above a per-severity threshold, with the severity × impact summary. Uses
the stdlib (``urllib``) — no extra HTTP dependency.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from statewatch.classifier import severity_rank
from statewatch.config import SlackConfig
from statewatch.report import Report

_SEV_EMOJI = {"CRITICAL": ":rotating_light:", "MEDIUM": ":warning:", "LOW": ":information_source:"}


class SlackError(RuntimeError):
    """Raised when the webhook POST fails."""


def _blocks(report: Report, cfg: SlackConfig) -> list[dict]:
    threshold = severity_rank(cfg.severity_threshold)
    shown = [f for f in report.findings if severity_rank(f.severity) >= threshold]
    if not shown:
        return []

    header = (
        f"statewatch: {len(shown)} finding(s) ≥ {cfg.severity_threshold} — "
        f"highest {report.highest_severity()}, exit {report.exit_code()}"
    )
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": header}}
    ]
    for f in shown:
        emoji = _SEV_EMOJI.get(f.severity, "")
        line = f"{emoji} *{f.severity}* `{f.resource_type}` *{f.name}* ({f.status})"
        if cfg.include_impact_summary:
            c = f.label_counts()
            line += (
                f"\n→ {c.get('DIRECT', 0)} DIRECT, "
                f"{c.get('INDIRECT', 0)} INDIRECT, {c.get('WATCH', 0)} WATCH"
            )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": line}})
    return blocks


def post_report(report: Report, cfg: SlackConfig) -> bool:
    """POST the report to Slack. Returns True if anything was sent (False if nothing met
    the threshold). Raises :class:`SlackError` on transport failure."""
    blocks = _blocks(report, cfg)
    if not blocks:
        return False
    payload = json.dumps({"blocks": blocks}).encode("utf-8")
    req = urllib.request.Request(
        cfg.webhook, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (trusted webhook)
            if resp.status >= 300:
                raise SlackError(f"Slack webhook returned HTTP {resp.status}")
    except urllib.error.URLError as exc:
        raise SlackError(f"Slack webhook POST failed: {exc}") from exc
    return True
