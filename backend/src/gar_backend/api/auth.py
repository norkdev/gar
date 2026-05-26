"""Authorization check at the API boundary.

v1: pass-through. The hook exists so multi-tenant authorization can be added
later without changing call sites. Spec §10 seam #7.
"""


def authorize(tenant_id: str = "default") -> bool:
    """Return whether the caller is authorized for the given tenant. v1 always allows."""
    return True
