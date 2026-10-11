"""Audit trail for security-relevant and business events.

Audit rows are written in the same transaction as the change they describe, tagged with the
current request id. Metadata must never carry emails, IP addresses, passwords, tokens, secrets or
throttle key hashes; `record` rejects keys that look like those as a programming error.
"""

import math
import re
import uuid
from collections.abc import Mapping
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import request_id_ctx
from app.models.audit_log import AuditLog
from app.repositories.audit_repository import AuditRepository

JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class AuditAction(StrEnum):
    AUTH_REGISTERED = "AUTH_REGISTERED"
    AUTH_LOGIN_SUCCEEDED = "AUTH_LOGIN_SUCCEEDED"
    AUTH_LOGIN_FAILED = "AUTH_LOGIN_FAILED"
    AUTH_LOGIN_THROTTLED = "AUTH_LOGIN_THROTTLED"
    AUTH_LOGIN_BLOCKED_ATTEMPT = "AUTH_LOGIN_BLOCKED_ATTEMPT"
    AUTH_REFRESH_REUSED = "AUTH_REFRESH_REUSED"
    AUTH_LOGGED_OUT = "AUTH_LOGGED_OUT"
    WORKSPACE_CREATED = "WORKSPACE_CREATED"
    MEMBER_ADDED = "MEMBER_ADDED"
    MEMBER_ROLE_CHANGED = "MEMBER_ROLE_CHANGED"
    MEMBER_REMOVED = "MEMBER_REMOVED"
    PRODUCT_CREATED = "PRODUCT_CREATED"
    PRODUCT_REVISION_CREATED = "PRODUCT_REVISION_CREATED"
    BOM_UPLOADED = "BOM_UPLOADED"
    BOM_PARSE_REQUESTED = "BOM_PARSE_REQUESTED"
    BOM_PARSED = "BOM_PARSED"
    BOM_PARSE_FAILED = "BOM_PARSE_FAILED"
    BOM_FILE_DOWNLOADED = "BOM_FILE_DOWNLOADED"


# Rejected anywhere in a key, case-insensitively.
_FORBIDDEN_KEY_SUBSTRINGS = ("password", "token", "secret", "email", "key_hash", "keyhash")
# "ip" is matched as a whole word of the key (`ip`, `client_ip`, `ipAddress`) so that keys such
# as `description` or `membership_id` stay usable.
_FORBIDDEN_KEY_WORDS = frozenset({"ip", "ips", "ipaddr", "ipaddress", "ipv4", "ipv6"})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD_SEPARATOR = re.compile(r"[^a-z0-9]+")


def _is_forbidden_key(key: str) -> bool:
    lowered = key.lower()
    if any(fragment in lowered for fragment in _FORBIDDEN_KEY_SUBSTRINGS):
        return True
    words = _WORD_SEPARATOR.split(_CAMEL_BOUNDARY.sub("_", key).lower())
    return any(word in _FORBIDDEN_KEY_WORDS for word in words)


def _clean_metadata(value: object, path: str) -> JsonValue:
    """Return a plain-JSON copy of `value`, or raise `ValueError` naming the offending path."""
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"Audit metadata value at {path!r} is not a finite number.")
        return value
    if isinstance(value, list):
        return [_clean_metadata(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        cleaned: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"Audit metadata key at {path!r} is not a string.")
            if _is_forbidden_key(key):
                raise ValueError(
                    f"Audit metadata key {key!r} looks sensitive; audit rows must never contain "
                    "emails, IP addresses, passwords, tokens, secrets or key hashes."
                )
            cleaned[key] = _clean_metadata(item, f"{path}.{key}")
        return cleaned
    raise ValueError(
        f"Audit metadata value at {path!r} has unsupported type {type(value).__name__}; "
        "use JSON primitives, lists and dicts (convert UUIDs and enums to str)."
    )


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._repository = AuditRepository(session)

    async def record(
        self,
        action: AuditAction,
        *,
        entity_type: str,
        entity_id: uuid.UUID | None = None,
        actor_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> AuditLog:
        """Add an audit row to the current transaction; the caller commits.

        Raises `ValueError` when `metadata` contains a sensitive-looking key or a value that is
        not plain JSON. Nothing is written in that case.
        """
        cleaned = _clean_metadata(metadata or {}, "metadata")
        assert isinstance(cleaned, dict)  # noqa: S101 - a Mapping always cleans to a dict
        entry = AuditLog(
            action=action.value,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=actor_id,
            workspace_id=workspace_id,
            action_metadata=cleaned,
            request_id=request_id_ctx.get(),
        )
        return await self._repository.add(entry)
