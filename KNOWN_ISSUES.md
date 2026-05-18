# Known issues / deferred work

Tracked, deliberately-deferred items. Each says *why* it was deferred and *when* it must
be addressed. A deferred fix that isn't written down is a forgotten bug.

## Resolved

### 1. Subnet ref dropped the region for bare-name inputs — RESOLVED in Phase 3
Resolved at Phase 3 entry as planned. `normalizer.subnetwork_ref_from_attr` now produces
a region-qualified canonical id for full-URL, path, **and** bare-name forms; for bare
names the region is derived from the instance's own zone (a GCP instance's subnet is
always in its region). `resources/subnetwork.py` builds its `resource_id` from the same
shared `subnetwork_id`, so an instance's inferred subnet ref and the subnet resource node
unify into one node. Regression locked by
`tests/test_resources.py::test_known_issue_1_subnet_ref_unifies_across_input_forms`.
Original report retained below for history.

<details><summary>Original deferral (Phase 2)</summary>

### 1. `_normalize_subnetwork_ref` drops the region for bare-name inputs
- **Where:** `src/statewatch/normalizer.py` — `_normalize_subnetwork_ref`.
- **What:** When a compute instance's `subnetwork` attribute is a *full URL* or a
  `projects/.../regions/.../subnetworks/...` path, the inferred `parent_ref` id keeps the
  region segment (`projects/<p>/regions/<r>/subnetworks/<n>`). When it's a *bare name*,
  the fallback emits `projects/<p>/subnetworks/<n>` — **no region**.
- **Why it matters:** Phase 3 makes `google_compute_subnetwork` a first-class normalized
  resource. Its node `resource_id` will be the regional form. If Phase 1's bare-name
  fallback id doesn't match that form, an inferred edge and the real subnet node will *not
  unify* — the graph will show a duplicate/dangling external node instead of connecting
  the instance to its actual subnet. This is exactly the "Phase 3 fights the graph"
  failure mode flagged during Phase 2 design.
- **Why deferred:** Fixing it in Phase 2 means churning Phase 1 normalization mid-phase
  with no Phase 2 consumer that needs it (the fixtures use full URLs, which are already
  correct). Approved for deferral during Phase 2 architectural review.
- **Fix at Phase 3 entry:** make the subnetwork (and, symmetrically, network/SA) id
  derivation produce the *same* canonical id the Phase 3 subnetwork normalizer will assign
  — region-qualified — and add a normalizer test asserting an instance's inferred subnet
  ref equals the subnet resource's `resource_id` for bare-name, path, and URL inputs.

</details>

### 2. `scan` degraded instead of failing when GCP auth was unavailable — RESOLVED in Phase 4
`adapters/gcp.py` now makes a real `AssetServiceClient.list_assets` call by default.
`scan` no longer degrades: a missing-credentials situation is a hard `typer.Exit(2)`
(`cli._produce_report`). Offline demo/CI is an explicit, separate path — `--stub` or
`STATEWATCH_STUB_GCP=1` — never a silent fallback. Original carry-forward retained below.

<details><summary>Original follow-up (Phases 1–3)</summary>

`scan` printed a warning and continued against stubbed CAI when ADC was absent, because
the live-state fetch was stubbed and degrading kept it demonstrable offline. The fix was
gated on a real `list_assets` call landing, which it did in Phase 4.

</details>

## Carried to v0.2 (none)

Nothing is being silently carried. v0.2 (drift attribution) is a separate, scoped release
per `spec.md`; it is not a deferred bug.

## Heuristics stated honestly (not bugs, but tracked)

- **Severity as a propagation proxy.** Impact labelling treats severity ≥ MEDIUM as
  "propagates." This is a deliberate heuristic, not dataflow analysis, and is stated as
  such in terminal output, JSON, and the README. A future enhancement could model
  per-attribute propagation; v0.1 intentionally does not.
- **Firewall applicability inference** matches on network + target tags/SAs, not a
  packet-level evaluation. Documented in `graph/inferred.py`.
