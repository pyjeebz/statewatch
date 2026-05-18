# Contributing to statewatch

statewatch is GCP-only in v0.1 by design. The two highest-value contributions are **new
cloud adapters** (AWS, Azure) and **new GCP resource types** — both have explicit
extension seams so you don't have to touch the engine.

## Dev setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q          # 40+ tests, all green
ruff check .
mypy src/statewatch
```

Offline run (no GCP project needed): `statewatch scan --tfstate
tests/fixtures/firewall_subnet_drift.tfstate.json --project demo-project --stub`.

## Adding a cloud adapter (AWS / Azure)

The boundary is `src/statewatch/adapters/base.py` — a `CloudAdapter` `Protocol`. An
adapter's only job is **auth + return live state as normalized `Resource` objects**. It
does not diff, classify, or know about Terraform.

Implement four things (see the protocol docstring for the full contract):

1. `name` — `"aws"` / `"azure"`.
2. `authenticate()` — establish credentials via the provider's standard mechanism and make
   one cheap call to validate them. Raise `AdapterAuthError` on failure.
3. `supported_resource_types()` — the Terraform type names you can fetch
   (e.g. `{"aws_instance", "aws_security_group"}`).
4. `fetch_resources(types, *, scope)` — query the provider inventory API
   (AWS Config / Azure Resource Graph) and map each native object into a `Resource`,
   deriving a `resource_id` that matches what Terraform-side normalization produces, and
   filling `parent_refs` from obvious attribute references.

Nothing else in the codebase needs to change — the differ, graph, classifier and impact
analyzer are provider-agnostic.

## Adding a GCP resource type

One module per type under `src/statewatch/resources/` (see `compute_instance.py`,
`firewall.py`, `subnetwork.py`, `gke_cluster.py` for the pattern):

1. Create `resources/<type>.py` with `RESOURCE_TYPE`, `normalize_from_tfstate(...)`, and
   `normalize_from_cai(...)`. Build `resource_id` from the shared id helpers in
   `normalizer.py` so it unifies with other resources' `parent_refs` (this is what makes
   the graph connect — see KNOWN_ISSUES history for why it matters).
2. Register it in `resources/__init__.py` (`_TF`, `_CAI`, `_CAI_ASSET_TO_TYPE`) and add
   the CAI asset type to `adapters/gcp.py`.
3. Whitelist only *comparable* attributes; never copy server-generated noise
   (`*_fingerprint`, `self_link`, ids, timestamps).
4. Add severity rules in `classifier.py` if the type has security-relevant changes.
5. Add a realistic fixture and tests. **Fixtures are the README of the test suite** —
   real-shaped `.tfstate`, not toy examples.

## Conventions

- Edge direction is `A → B` = "A depends on B"; impact flows against it (predecessors).
- Don't overclaim: severity is a heuristic proxy for propagation, stated as such in
  output and docs. Keep it that way.
- Deferred work goes in `KNOWN_ISSUES.md` with *why* and *when*, never silently.
- ruff + mypy clean; tests green; honest commit messages (phase/scope-enabling edits to
  shared modules committed separately).

## Out of scope (v0.1)

Auto-remediation, multi-cloud in one run, a web dashboard, a SaaS, and replacing
`terraform plan`. See "What this is NOT trying to be" in `spec.md`.
