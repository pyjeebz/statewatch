"""GCP adapter — live resource state via Cloud Asset Inventory.

The **authentication flow is real** (Application Default Credentials via
``google.auth.default()``), but :meth:`GCPAdapter.fetch_resources` returns *stubbed*
CAI-shaped data rather than calling ``AssetServiceClient.list_assets``. The stub is shaped
like real CAI responses and deliberately drifts from the Phase 3 example state
(``tests/fixtures/firewall_subnet_drift.tfstate.json``) so ``statewatch scan`` shows
severity × impact end to end. Replacing the stub with the real ``list_assets`` call is a
self-contained follow-up — see the TODO below.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from statewatch.adapters.base import AdapterAuthError, AdapterError
from statewatch.normalizer import Resource
from statewatch.resources import (
    SUPPORTED_RESOURCE_TYPES,
    normalize_cai,
)

_TYPE_TO_CAI_ASSET_TYPE = {
    "google_compute_instance": "compute.googleapis.com/Instance",
    "google_compute_firewall": "compute.googleapis.com/Firewall",
    "google_compute_subnetwork": "compute.googleapis.com/Subnetwork",
}
_SUPPORTED = frozenset(_TYPE_TO_CAI_ASSET_TYPE) & SUPPORTED_RESOURCE_TYPES


class GCPAdapter:
    """CloudAdapter implementation backed by Google Cloud Asset Inventory."""

    name = "gcp"

    def __init__(self) -> None:
        self._credentials: Any | None = None
        self._default_project: str | None = None

    # -- CloudAdapter protocol ---------------------------------------------------------

    def authenticate(self) -> None:
        """Resolve Application Default Credentials.

        Uses ``google.auth.default()`` — the same resolution order as ``gcloud`` and every
        Google client library. Raises :class:`AdapterAuthError` with remediation guidance
        if no credentials are found.
        """
        try:
            import google.auth
            from google.auth.exceptions import DefaultCredentialsError
        except ImportError as exc:  # pragma: no cover - dependency declared in pyproject
            raise AdapterError(
                "google-auth is required for the GCP adapter; install statewatch with its "
                "dependencies (pip install statewatch)."
            ) from exc

        try:
            credentials, project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        except DefaultCredentialsError as exc:
            raise AdapterAuthError(
                "No Google Cloud credentials found. Run "
                "`gcloud auth application-default login`, or set "
                "GOOGLE_APPLICATION_CREDENTIALS to a service-account key file."
            ) from exc

        self._credentials = credentials
        self._default_project = project

    def supported_resource_types(self) -> frozenset[str]:
        return _SUPPORTED

    def fetch_resources(
        self,
        resource_types: Iterable[str],
        *,
        scope: str,
    ) -> list[Resource]:
        requested = list(resource_types)
        unsupported = [t for t in requested if t not in _SUPPORTED]
        if unsupported:
            raise AdapterError(
                f"GCP adapter does not support resource types: {sorted(unsupported)} "
                f"(supported: {sorted(_SUPPORTED)})"
            )

        resources: list[Resource] = []
        for asset in self._list_assets(project=scope):
            asset_type = asset.get("asset_type")
            rtype = next(
                (t for t, a in _TYPE_TO_CAI_ASSET_TYPE.items() if a == asset_type), None
            )
            if rtype not in requested:
                continue
            r = normalize_cai(asset, project=scope)
            if r is not None:
                resources.append(r)
        return resources

    # -- CAI calls (stubbed) -----------------------------------------------------------

    def _list_assets(self, *, project: str) -> list[dict[str, Any]]:
        """Return CAI ``Asset`` dicts for the supported asset types in ``project``.

        TODO (follow-up): replace this stub with a real call:

            from google.cloud import asset_v1
            client = asset_v1.AssetServiceClient(credentials=self._credentials)
            pager = client.list_assets(request={
                "parent": f"projects/{project}",
                "asset_types": sorted(set(_TYPE_TO_CAI_ASSET_TYPE.values())),
                "content_type": asset_v1.ContentType.RESOURCE,
            })
            return [json.loads(asset_v1.Asset.to_json(a)) for a in pager]

        Until then we return realistic, hand-built data that drifts from the Phase 3
        example state so the scan pipeline is exercisable offline.
        """
        return _stub_assets(project)


# --------------------------------------------------------------------------------------
# Stubbed CAI data — drifts from tests/fixtures/firewall_subnet_drift.tfstate.json
# --------------------------------------------------------------------------------------

_REGION = "us-central1"


def _instance_asset(
    project: str,
    name: str,
    zone: str,
    machine_type: str,
    *,
    sa_email: str,
    tags: list[str],
) -> dict[str, Any]:
    return {
        "name": f"//compute.googleapis.com/projects/{project}/zones/{zone}/instances/{name}",
        "asset_type": "compute.googleapis.com/Instance",
        "resource": {
            "data": {
                "name": name,
                "zone": f"https://www.googleapis.com/compute/v1/projects/{project}/zones/{zone}",
                "machineType": (
                    f"https://www.googleapis.com/compute/v1/projects/{project}"
                    f"/zones/{zone}/machineTypes/{machine_type}"
                ),
                "status": "RUNNING",
                "canIpForward": False,
                "deletionProtection": False,
                "labels": {"env": "prod"},
                "tags": {"items": tags},
                "metadata": {"items": [{"key": "enable-oslogin", "value": "TRUE"}]},
                "networkInterfaces": [
                    {
                        "name": "nic0",
                        "network": (
                            f"https://www.googleapis.com/compute/v1/projects/{project}"
                            f"/global/networks/prod-vpc"
                        ),
                        "subnetwork": (
                            f"https://www.googleapis.com/compute/v1/projects/{project}"
                            f"/regions/{_REGION}/subnetworks/prod-subnet"
                        ),
                        "networkIP": "10.0.0.10",
                        "accessConfigs": [],
                    }
                ],
                "serviceAccounts": [
                    {"email": sa_email, "scopes": ["https://www.googleapis.com/auth/cloud-platform"]}
                ],
                "scheduling": {"preemptible": False, "automaticRestart": True},
            }
        },
    }


def _stub_assets(project: str) -> list[dict[str, Any]]:
    """Live state: instances match Terraform; the subnet and firewall have drifted.

    - subnet ``prod-subnet``: ``ipCidrRange`` 10.0.0.0/24 -> 10.0.0.0/20 (MEDIUM, but
      every instance in the subnet is DIRECTly impacted -> wide blast radius).
    - firewall ``allow-http``: ``sourceRanges`` 10.0.0.0/8 -> 0.0.0.0/0 (CRITICAL;
      DIRECTly impacts the http-server-tagged instances).
    - instances: unchanged, so the only drift is on the high-blast-radius resources.
    """
    sa = f"sa-app@{project}.iam.gserviceaccount.com"
    return [
        _instance_asset(project, "api-server-prod", "us-central1-a", "n2-standard-4",
                        sa_email=sa, tags=["http-server", "ssh"]),
        _instance_asset(project, "web-2", "us-central1-a", "e2-standard-2",
                        sa_email=sa, tags=["http-server"]),
        _instance_asset(project, "worker-01", "us-central1-b", "e2-medium",
                        sa_email=sa, tags=["worker"]),
        {
            "name": (
                f"//compute.googleapis.com/projects/{project}"
                f"/regions/{_REGION}/subnetworks/prod-subnet"
            ),
            "asset_type": "compute.googleapis.com/Subnetwork",
            "resource": {
                "data": {
                    "name": "prod-subnet",
                    "region": f"https://www.googleapis.com/compute/v1/projects/{project}/regions/{_REGION}",
                    "network": (
                        f"https://www.googleapis.com/compute/v1/projects/{project}"
                        f"/global/networks/prod-vpc"
                    ),
                    "ipCidrRange": "10.0.0.0/20",  # drift: tfstate says /24
                    "privateIpGoogleAccess": True,
                    "secondaryIpRanges": [
                        {"rangeName": "pods", "ipCidrRange": "10.4.0.0/14"}
                    ],
                    "purpose": "PRIVATE",
                }
            },
        },
        {
            "name": f"//compute.googleapis.com/projects/{project}/global/firewalls/allow-http",
            "asset_type": "compute.googleapis.com/Firewall",
            "resource": {
                "data": {
                    "name": "allow-http",
                    "network": (
                        f"https://www.googleapis.com/compute/v1/projects/{project}"
                        f"/global/networks/prod-vpc"
                    ),
                    "direction": "INGRESS",
                    "priority": 1000,
                    "disabled": False,
                    "sourceRanges": ["0.0.0.0/0"],  # drift: tfstate says 10.0.0.0/8
                    "targetTags": ["http-server"],
                    "allowed": [{"IPProtocol": "tcp", "ports": ["80", "443"]}],
                }
            },
        },
    ]
