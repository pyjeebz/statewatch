"""Combine drift + severity + impact into one report, and decide the exit code.

A *finding* is one drifted resource carrying its severity (how bad the change is) and its
blast radius (who depends on it, labelled DIRECT/INDIRECT/WATCH). Exit code follows the
agreed severity × impact matrix: severity sets the floor, blast radius can raise MEDIUM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from statewatch.classifier import CRITICAL, LOW, MEDIUM, classify_resource_diff, max_severity
from statewatch.differ import Change, ResourceDiff
from statewatch.graph.builder import Graph
from statewatch.impact import rules
from statewatch.impact.analyzer import ImpactedNode, analyze_impact

# "Significant blast radius" threshold (hardcoded heuristic for v0.1; configurable later).
SIGNIFICANT_DIRECT = 3


@dataclass
class Finding:
    resource_id: str
    resource_type: str
    name: str
    status: str  # "drifted" | "missing_in_live" | "unmanaged"
    severity: str
    changes: list[Change] = field(default_factory=list)
    change_severity: dict[str, str] = field(default_factory=dict)
    impacted: list[ImpactedNode] = field(default_factory=list)

    def direct_count(self) -> int:
        return sum(1 for n in self.impacted if n.label == rules.DIRECT)

    def label_counts(self) -> dict[str, int]:
        counts = {rules.DIRECT: 0, rules.INDIRECT: 0, rules.WATCH: 0}
        for n in self.impacted:
            counts[n.label] = counts.get(n.label, 0) + 1
        return counts


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def highest_severity(self) -> str:
        sev = "NONE"
        for f in self.findings:
            sev = max_severity(sev, f.severity)
        return sev

    def exit_code(self) -> int:
        """0 clean · 1 low/medium drift · 2 critical OR significant blast radius."""
        if not self.findings:
            return 0
        highest = self.highest_severity()
        significant = any(
            f.severity in (MEDIUM, CRITICAL) and f.direct_count() >= SIGNIFICANT_DIRECT
            for f in self.findings
        )
        if highest == CRITICAL or significant:
            return 2
        if highest in (LOW, MEDIUM):
            return 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "findings": len(self.findings),
                "highest_severity": self.highest_severity(),
                "exit_code": self.exit_code(),
            },
            "findings": [
                {
                    "resource_id": f.resource_id,
                    "resource_type": f.resource_type,
                    "name": f.name,
                    "status": f.status,
                    "severity": f.severity,
                    "changes": [
                        {
                            "path": c.path,
                            "old_value": c.old_value,
                            "new_value": c.new_value,
                            "change_kind": c.change_kind,
                            "severity": f.change_severity.get(c.path),
                        }
                        for c in f.changes
                    ],
                    "impacted": [
                        {
                            "resource_id": n.resource_id,
                            "resource_type": n.resource_type,
                            "name": n.name,
                            "distance": n.distance,
                            "label": n.label,
                            "external": n.external,
                        }
                        for n in f.impacted
                    ],
                }
                for f in self.findings
            ],
        }


def build_report(diffs: list[ResourceDiff], graph: Graph) -> Report:
    """Fold differ output + severity + graph impact into a :class:`Report`."""
    report = Report()
    for rd in diffs:
        severity, per_path = classify_resource_diff(rd)
        propagating = rules.is_propagating(severity)
        impacted = analyze_impact(graph, rd.resource_id, propagating=propagating)
        report.findings.append(
            Finding(
                resource_id=rd.resource_id,
                resource_type=rd.resource_type,
                name=rd.name,
                status=rd.status,
                severity=severity,
                changes=list(rd.changes),
                change_severity=per_path,
                impacted=impacted,
            )
        )
    # Loudest first: severity desc, then DIRECT-impact count desc.
    report.findings.sort(
        key=lambda f: (-_sev_rank(f.severity), -f.direct_count(), f.resource_id)
    )
    return report


def _sev_rank(sev: str) -> int:
    from statewatch.classifier import severity_rank

    return severity_rank(sev)
