"""GCP adapter — live resource state via Cloud Asset Inventory.

Phase 1 status: the **authentication flow is real** (Application Default Credentials via
``google.auth.default()``), but :meth:`GCPAdapter.fetch_resources` returns *stubbed*
CAI-shaped data rather than calling ``AssetServiceClient.list_assets``. The stub is shaped
like a real CAI response and deliberately contains drift relative to the example Terraform
state, so ``statewatch scan`` demonstrates an end-to-end diff today. Replacing the stub
with the real ``list_assets`` call is a self-contained follow-up — see the TODO below.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from statewatch.adapters.base import AdapterAuthError, AdapterError
from statewatch.normalizer import Resource, normalize_compute_instance_from_cai

# Terraform type name -> Cloud Asset Inventory asset type. Phase 1 supports one type;
# firewall / subnetwork / GKE entries get added here in Phases 3-4.
_TYPE_TO_CAI_ASSET_TYPE = {
    "google_compute_instance": "compute.googleapis.com/Instance",
}

_SUPPORTED = frozenset(_TYPE_TO_CAI_ASSET_TYPE)


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
        Google client library: ``GOOGLE_APPLICATION_CREDENTIALS``, ``gcloud auth
        application-default login`` credentials, then the metadata server on GCE/GKE/Cloud
        Run. Raises :class:`AdapterAuthError` with remediation guidance if none are found.
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
        for rtype in requested:
            if rtype == "google_compute_instance":
                for asset in self._list_compute_instances(project=scope):
                    resources.append(
                        normalize_compute_instance_from_cai(asset, project=scope)
                    )
        return resources

    # -- CAI calls (stubbed in Phase 1) ------------------------------------------------

    def _list_compute_instances(self, *, project: str) -> list[dict[str, Any]]:
        """Return CAI ``Asset`` dicts for ``compute.googleapis.com/Instance`` in ``project``.

        TODO (Phase 1 follow-up): replace this stub with a real call:

            from google.cloud import asset_v1
            client = asset_v1.AssetServiceClient(credentials=self._credentials)
            pager = client.list_assets(request={
                "parent": f"projects/{project}",
                "asset_types": ["compute.googleapis.com/Instance"],
                "content_type": asset_v1.ContentType.RESOURCE,
            })
            return [json.loads(asset_v1.Asset.to_json(a)) for a in pager]

        Until then we return realistic, hand-built data that drifts from the example
        Terraform state so the scan pipeline is exercisable.
        """
        return _stub_compute_instances(project)


def _machine_type_url(project: str, zone: str, machine_type: str) -> str:
    return (
        f"https://www.googleapis.com/compute/v1/projects/{project}"
        f"/zones/{zone}/machineTypes/{machine_type}"
    )


def _stub_compute_instances(project: str) -> list[dict[str, Any]]:
    """Hand-built CAI-shaped instances that intentionally drift from the example tfstate.

    - ``api-server-prod``: machine type drifted n2-standard-4 -> n2-standard-8, a new
      ``block-project-ssh-keys`` metadata entry appeared, and a public IP was attached.
    - ``worker-01``: matches Terraform exactly (no drift).
    - ``orphan-debug-vm``: exists live but is not in Terraform state (unmanaged).
    """

    def instance(
        name: str,
        zone: str,
        machine_type: str,
        *,
        subnetwork: str,
        network: str,
        sa_email: str,
        metadata_items: list[dict[str, str]],
        labels: dict[str, str],
        tags: list[str],
        external_ip: str | None = None,
        preemptible: bool = False,
    ) -> dict[str, Any]:
        access_configs = (
            [{"name": "External NAT", "natIP": external_ip, "type": "ONE_TO_ONE_NAT"}]
            if external_ip
            else []
        )
        return {
            "name": (
                f"//compute.googleapis.com/projects/{project}/zones/{zone}/instances/{name}"
            ),
            "asset_type": "compute.googleapis.com/Instance",
            "resource": {
                "version": "v1",
                "discovery_document_uri": "https://www.googleapis.com/discovery/v1/apis/compute/v1/rest",
                "discovery_name": "Instance",
                "parent": f"//cloudresourcemanager.googleapis.com/projects/{project}",
                "data": {
                    "name": name,
                    "zone": (
                        f"https://www.googleapis.com/compute/v1/projects/{project}/zones/{zone}"
                    ),
                    "machineType": _machine_type_url(project, zone, machine_type),
                    "status": "RUNNING",
                    "canIpForward": False,
                    "deletionProtection": False,
                    "labels": labels,
                    "tags": {"items": tags},
                    "metadata": {"items": metadata_items},
                    "networkInterfaces": [
                        {
                            "name": "nic0",
                            "network": (
                                f"https://www.googleapis.com/compute/v1/projects/{project}"
                                f"/global/networks/{network}"
                            ),
                            "subnetwork": (
                                f"https://www.googleapis.com/compute/v1/projects/{project}"
                                f"/regions/{zone.rsplit('-', 1)[0]}/subnetworks/{subnetwork}"
                            ),
                            "networkIP": "10.0.0.10" if name == "api-server-prod" else "10.0.0.20",
                            "accessConfigs": access_configs,
                        }
                    ],
                    "serviceAccounts": [
                        {
                            "email": sa_email,
                            "scopes": ["https://www.googleapis.com/auth/cloud-platform"],
                        }
                    ],
                    "scheduling": {"preemptible": preemptible, "automaticRestart": not preemptible},
                },
            },
        }

    return [
        instance(
            "api-server-prod",
            "us-central1-a",
            "n2-standard-8",  # drift: tfstate says n2-standard-4
            subnetwork="prod-subnet",
            network="prod-vpc",
            sa_email="sa-api@" + project + ".iam.gserviceaccount.com",
            metadata_items=[
                {"key": "enable-oslogin", "value": "TRUE"},
                {"key": "block-project-ssh-keys", "value": "true"},  # drift: new key
            ],
            labels={"env": "prod", "team": "platform"},
            tags=["http-server", "ssh"],
            external_ip="34.120.55.10",  # drift: instance was private in tfstate
        ),
        instance(
            "worker-01",
            "us-central1-b",
            "e2-medium",  # matches tfstate — no drift
            subnetwork="prod-subnet",
            network="prod-vpc",
            sa_email="sa-worker@" + project + ".iam.gserviceaccount.com",
            metadata_items=[{"key": "enable-oslogin", "value": "TRUE"}],
            labels={"env": "prod", "team": "data"},
            tags=["worker"],
            preemptible=True,
        ),
        instance(
            "orphan-debug-vm",
            "us-central1-a",
            "e2-small",
            subnetwork="prod-subnet",
            network="prod-vpc",
            sa_email="sa-default@" + project + ".iam.gserviceaccount.com",
            metadata_items=[],
            labels={"env": "prod"},
            tags=["debug"],
        ),
    ]
