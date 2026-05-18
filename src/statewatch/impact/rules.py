"""Impact labelling rules — DIRECT / INDIRECT / WATCH.

A function of BOTH graph distance and drift type (the agreed Phase 3 decision):

* distance 1 -> DIRECT, distance 2 -> INDIRECT, distance >= 3 -> WATCH (always).
* Override everything to WATCH if the drift is *non-propagating* — i.e. the drifted
  resource's overall severity is LOW (labels / description / non-security tags). SPEC:
  "a label change on a parent resource is technically DIRECT but rarely meaningful."

Propagation is proxied by severity (>= MEDIUM). This is a heuristic, not dataflow
analysis — stated honestly rather than overclaimed.
"""

from __future__ import annotations

from statewatch.classifier import LOW, NONE

DIRECT = "DIRECT"
INDIRECT = "INDIRECT"
WATCH = "WATCH"

# For summarizing / sorting: DIRECT is the loudest.
LABEL_ORDER = {DIRECT: 0, INDIRECT: 1, WATCH: 2}


def is_propagating(overall_severity: str) -> bool:
    """Does a drift of this severity meaningfully propagate to dependents?

    LOW (and NONE) drift does not — its impacts are WATCH regardless of distance.
    """
    return overall_severity not in (LOW, NONE)


def label_for(distance: int, propagating: bool) -> str:
    """Label one impacted node given its predecessor distance and propagation."""
    if not propagating:
        return WATCH
    if distance <= 1:
        return DIRECT
    if distance == 2:
        return INDIRECT
    return WATCH
