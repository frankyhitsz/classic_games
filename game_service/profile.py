"""Canonical local profile identity rules shared by every data path."""

from __future__ import annotations

import unicodedata
import uuid
from dataclasses import dataclass

DEFAULT_PROFILE_NAME = "guest"


class ProfileIdentityError(ValueError):
    pass


@dataclass(frozen=True)
class ProfileIdentity:
    profile_id: str

    @staticmethod
    def normalize_display_name(value: str, *, maximum: int = 32) -> str:
        if not isinstance(value, str):
            raise ProfileIdentityError("display name must be a string")
        value = unicodedata.normalize("NFC", value).strip()
        if not value:
            value = "anonymous"
        if len(value) > maximum:
            raise ProfileIdentityError(
                f"display name must contain at most {maximum} characters")
        if any(unicodedata.category(char).startswith("C") for char in value):
            raise ProfileIdentityError("display name contains control characters")
        return value

    @classmethod
    def from_legacy_name(cls, display_name: str) -> "ProfileIdentity":
        name = cls.normalize_display_name(display_name)
        if name.casefold() in {"guest", "anonymous"}:
            name = DEFAULT_PROFILE_NAME
        return cls(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"classic-games-local-profile:{name}").hex)

    @classmethod
    def default(cls) -> "ProfileIdentity":
        return cls.from_legacy_name(DEFAULT_PROFILE_NAME)

    @classmethod
    def validate_uuid(cls, value: str) -> "ProfileIdentity":
        if not isinstance(value, str):
            raise ProfileIdentityError("profile_id must be a string")
        normalized = value.strip().lower()
        if (len(normalized) != 32
                or any(char not in "0123456789abcdef" for char in normalized)):
            raise ProfileIdentityError(
                "profile_id must be a 32-character UUID")
        return cls(normalized)

    @classmethod
    def resolve(cls, value: str | None,
                display_name: str) -> "ProfileIdentity":
        return (cls.from_legacy_name(display_name) if value is None
                else cls.validate_uuid(value))
