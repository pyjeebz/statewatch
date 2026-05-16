"""The ``CloudAdapter`` interface.

This protocol is the seam that keeps statewatch cloud-agnostic at the boundary even
though v0.1 only ships a GCP implementation. An adapter has exactly one job: authenticate
to a cloud, and return the *live* state of requested resource types as normalized
:class:`statewatch.normalizer.Resource` objects.

An adapter does **not**:
  * read or know anything about Terraform state,
  * compute diffs,
  * classify severity, or
  * traverse the dependency graph.

Those are all downstream of the adapter. If you want to add support for another cloud,
implement this protocol and nothing else changes.

Implementing a new adapter (e.g. AWS)
-------------------------------------
1. ``name``: a short provider key, e.g. ``"aws"``.
2. ``authenticate()``: establish credentials using the provider's standard mechanism
   (GCP: Application Default Credentials; AWS: the default boto3 credential chain; Azure:
   ``DefaultAzureCredential``). Make one cheap call to confirm the credentials work (e.g.
   STS ``GetCallerIdentity``). Raise :class:`AdapterAuthError` on failure.
3. ``supported_resource_types()``: the Terraform-style type names you can fetch live state
   for, e.g. ``{"aws_instance", "aws_security_group"}``. The CLI validates the user's
   requested types against this set.
4. ``fetch_resources(types, scope=...)``: query the provider's inventory API
   (GCP: Cloud Asset Inventory; AWS: AWS Config / describe APIs; Azure: Resource Graph),
   and map each native object into a ``Resource`` — deriving a ``resource_id`` that will
   match the one Terraform-side normalization produces, filling ``parent_refs`` from
   obvious attribute references, and stripping provider noise. Raise
   :class:`AdapterError` on API or permission failures.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from statewatch.normalizer import Resource


class AdapterError(RuntimeError):
    """Base class for adapter failures (API errors, permissions, bad responses)."""


class AdapterAuthError(AdapterError):
    """Raised when an adapter cannot establish or validate credentials."""


@runtime_checkable
class CloudAdapter(Protocol):
    """A provider-specific source of live resource state."""

    #: Short, stable provider key, e.g. ``"gcp"``. Used in CLI options and output.
    name: str

    def authenticate(self) -> None:
        """Establish and validate credentials for this provider.

        Called once before any :meth:`fetch_resources` call. Implementations should make a
        single inexpensive request to confirm the credentials are usable, and raise
        :class:`AdapterAuthError` (with an actionable message) if not.
        """
        ...

    def supported_resource_types(self) -> frozenset[str]:
        """Return the Terraform-style resource type names this adapter can fetch.

        e.g. ``frozenset({"google_compute_instance"})``. The CLI uses this to reject
        requests for unsupported types up front.
        """
        ...

    def fetch_resources(
        self,
        resource_types: Iterable[str],
        *,
        scope: str,
    ) -> list[Resource]:
        """Fetch live state for the given resource types within ``scope``.

        Args:
            resource_types: Terraform-style type names to fetch. Every value should be a
                member of :meth:`supported_resource_types`.
            scope: The provider-specific account/project scope to query. For GCP this is a
                project id; for AWS an account id (optionally region-qualified); for Azure
                a subscription id.

        Returns:
            Normalized :class:`Resource` objects for every matching live resource. The
            ``resource_id`` of each must be derivable identically from the corresponding
            Terraform state entry so the differ can pair them.

        Raises:
            AdapterError: on API errors, permission problems, or malformed responses.
        """
        ...
