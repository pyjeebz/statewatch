# Known issues / deferred work

Tracked, deliberately-deferred items. Each says *why* it was deferred and *when* it must
be addressed. A deferred fix that isn't written down is a forgotten bug.

## Phase 3 entry tasks (address before/at the start of Phase 3)

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

## Phase 1 follow-ups (carried forward)

### 2. `scan` degrades instead of failing when GCP auth is unavailable
- **Where:** `src/statewatch/cli.py` — `scan`, `AdapterAuthError` handler.
- **What:** With no Application Default Credentials, `scan` prints a warning and continues
  against the **stubbed** CAI fetch instead of exiting non-zero.
- **Why deferred:** In Phase 1 the live-state fetch is stubbed, so missing credentials
  aren't actually fatal yet; degrading keeps `scan` demonstrable offline.
- **Fix when:** the moment `GCPAdapter._list_compute_instances` makes a real
  `list_assets` call (Phase 1 follow-up / Phase 4 GCS+real-CAI work). At that point a
  missing-credentials situation MUST become a hard `typer.Exit(2)`. The code carries an
  inline TODO at the call site.
