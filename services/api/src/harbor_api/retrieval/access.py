"""The single definition of what a caller may retrieve.

Every chunk query goes through `AccessScope.sql_filter()`. Nothing else may query the
chunks table — that is the rule that keeps permission-aware retrieval honest, and the
integration tests assert it by probing the raw candidate lists rather than the answers.

Today the caller's groups come from a demo role header. In M2 they come from Better Auth
organisation membership; only `scope_for_role` changes.
"""

import uuid
from dataclasses import dataclass

from harbor_api.models.content import Visibility

# Demo roles → the group keys a caller holds. Visitors are anonymous customer-channel
# users (widget/WhatsApp); staff and management are signed-in users in M2.
ROLE_GROUPS: dict[str, tuple[str, ...]] = {
    "visitor": ("everyone",),
    "staff": ("everyone", "staff"),
    "management": ("everyone", "staff", "management"),
}

ROLE_VISIBILITY: dict[str, tuple[Visibility, ...]] = {
    "visitor": (Visibility.PUBLIC,),
    "staff": (Visibility.PUBLIC, Visibility.INTERNAL),
    "management": (Visibility.PUBLIC, Visibility.INTERNAL, Visibility.RESTRICTED),
}


@dataclass(frozen=True)
class AccessScope:
    tenant_id: uuid.UUID
    role: str
    principals: tuple[uuid.UUID, ...]
    visibilities: tuple[Visibility, ...]

    @property
    def sql_filter(self) -> str:
        """SQL fragment applied to every chunk query.

        Two independent checks: the document's visibility class, and an overlap between
        the chunk's allowed principals and the caller's groups. Either alone would be
        enough for the demo corpus; together they fail closed if one is mis-seeded.
        """
        # Values are cast explicitly so asyncpg never has to infer array types.
        return (
            "c.tenant_id = CAST(:tenant_id AS uuid) "
            "AND c.visibility = ANY(CAST(:visibilities AS text[])) "
            "AND c.allowed_principals && CAST(:principals AS uuid[])"
        )

    @property
    def params(self) -> dict[str, object]:
        return {
            "tenant_id": str(self.tenant_id),
            "visibilities": [v.value for v in self.visibilities],
            "principals": [str(principal) for principal in self.principals],
        }


def scope_for_role(tenant_id: uuid.UUID, role: str, groups: dict[str, uuid.UUID]) -> AccessScope:
    if role not in ROLE_GROUPS:
        raise ValueError(f"unknown role: {role}")
    return AccessScope(
        tenant_id=tenant_id,
        role=role,
        principals=tuple(groups[key] for key in ROLE_GROUPS[role] if key in groups),
        visibilities=ROLE_VISIBILITY[role],
    )
