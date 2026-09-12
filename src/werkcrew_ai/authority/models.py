"""Immutable AUTH-0 company authority and trusted-principal evidence.

These records become authoritative only when restored from the privileged
deployment/bootstrap repository.  Constructing a dataclass or knowing an ID is
not authentication.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, replace
from enum import StrEnum

from werkcrew_ai.field.serialization import (
    canonical_json,
    deserialize_worker_registry,
    sha256_text,
)


AUTHORITY_ROOT_SCHEMA_VERSION = "company-authority-root-v1"
TRUSTED_PRINCIPAL_SCHEMA_VERSION = "trusted-principal-v1"
AUTHORITY_RULE_VERSION = "auth0-deployment-bootstrap-v1"
WORKER_REGISTRY_SCHEMA_VERSION = "m2-worker-registry-v1"


class AuthorityError(ValueError):
    def __init__(self, code: str, field_name: str) -> None:
        self.code = code
        self.field_name = field_name
        super().__init__(f"{code}: {field_name}")


class AuthorityValidationError(AuthorityError):
    pass


class AuthorityBindingError(AuthorityError):
    pass


class AuthorityStorageError(AuthorityError):
    pass


class AuthorityConflictError(AuthorityError):
    pass


class ProvisioningSource(StrEnum):
    DEPLOYMENT_BOOTSTRAP = "DEPLOYMENT_BOOTSTRAP"


class PrincipalType(StrEnum):
    OWNER = "OWNER"
    WORKER = "WORKER"


def _require(condition: bool, field_name: str) -> None:
    if not condition:
        raise AuthorityValidationError("INVALID_VALUE", field_name)


def _identity(value: str, field_name: str) -> None:
    _require(type(value) is str and bool(value) and value == value.strip(), field_name)
    _require(
        not any(
            ord(character) < 32
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        ),
        field_name,
    )


def _digest(value: str, field_name: str) -> None:
    _require(
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value),
        field_name,
    )


def _primitive(value):
    if isinstance(value, StrEnum):
        return value.value
    return value


def _semantic_json(value, omitted: tuple[str, ...]) -> str:
    return canonical_json(
        {
            item.name: _primitive(getattr(value, item.name))
            for item in fields(value)
            if item.name not in omitted
        }
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyAuthorityRoot:
    """One deployment-provisioned authority scope over an existing CompanyPlan."""

    company_id: str
    company_plan_id: str
    company_plan_provenance_reference: str
    owner_principal_subject_id: str
    provisioning_reference: str
    worker_registry_revision: int
    worker_registry_fingerprint: str
    canonical_worker_registry_json: str
    provisioning_source: ProvisioningSource = field(
        default=ProvisioningSource.DEPLOYMENT_BOOTSTRAP,
        init=False,
    )
    worker_registry_schema_version: str = field(
        default=WORKER_REGISTRY_SCHEMA_VERSION,
        init=False,
    )
    schema_version: str = field(default=AUTHORITY_ROOT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=AUTHORITY_RULE_VERSION, init=False)
    authority_root_fingerprint: str = field(init=False)
    authority_root_id: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "company_id",
            "company_plan_id",
            "company_plan_provenance_reference",
            "owner_principal_subject_id",
            "provisioning_reference",
        ):
            _identity(getattr(self, name), name)
        _require(
            type(self.worker_registry_revision) is int
            and self.worker_registry_revision >= 0,
            "worker_registry_revision",
        )
        _digest(self.worker_registry_fingerprint, "worker_registry_fingerprint")
        _require(
            type(self.canonical_worker_registry_json) is str,
            "canonical_worker_registry_json",
        )
        try:
            registry = deserialize_worker_registry(self.canonical_worker_registry_json)
        except (TypeError, ValueError, OverflowError, RecursionError) as error:
            raise AuthorityValidationError(
                "INVALID_VALUE", "canonical_worker_registry_json"
            ) from error
        _require(
            registry.registry_revision == self.worker_registry_revision,
            "worker_registry_revision",
        )
        _require(
            sha256_text(self.canonical_worker_registry_json)
            == self.worker_registry_fingerprint,
            "worker_registry_fingerprint",
        )
        digest = sha256_text(company_authority_root_semantic_json(self))
        object.__setattr__(self, "authority_root_fingerprint", digest)
        object.__setattr__(
            self,
            "authority_root_id",
            "auth0-company-authority-root-" + digest,
        )

    @property
    def worker_ids(self) -> tuple[str, ...]:
        return deserialize_worker_registry(
            self.canonical_worker_registry_json
        ).worker_ids


def company_authority_root_semantic_json(value: CompanyAuthorityRoot) -> str:
    return _semantic_json(
        value,
        ("authority_root_fingerprint", "authority_root_id"),
    )


def serialize_company_authority_root(value: CompanyAuthorityRoot) -> str:
    _require(
        type(value) is CompanyAuthorityRoot and replace(value) == value,
        "authority_root",
    )
    return canonical_json(
        {item.name: _primitive(getattr(value, item.name)) for item in fields(value)}
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class TrustedPrincipal:
    """Content-addressed principal binding; never a request credential."""

    authority_root_id: str
    authority_root_fingerprint: str
    company_id: str
    company_plan_id: str
    principal_subject_id: str
    principal_type: PrincipalType
    worker_id: str | None
    worker_registry_revision: int
    worker_registry_fingerprint: str
    schema_version: str = field(default=TRUSTED_PRINCIPAL_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=AUTHORITY_RULE_VERSION, init=False)
    principal_fingerprint: str = field(init=False)
    principal_id: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "authority_root_id",
            "company_id",
            "company_plan_id",
            "principal_subject_id",
        ):
            _identity(getattr(self, name), name)
        _digest(self.authority_root_fingerprint, "authority_root_fingerprint")
        _require(
            self.authority_root_id
            == "auth0-company-authority-root-" + self.authority_root_fingerprint,
            "authority_root_id",
        )
        _require(type(self.principal_type) is PrincipalType, "principal_type")
        _require(
            type(self.worker_registry_revision) is int
            and self.worker_registry_revision >= 0,
            "worker_registry_revision",
        )
        _digest(self.worker_registry_fingerprint, "worker_registry_fingerprint")
        if self.principal_type is PrincipalType.OWNER:
            _require(self.worker_id is None, "worker_id")
        else:
            _require(type(self.worker_id) is str, "worker_id")
            _identity(self.worker_id, "worker_id")
        digest = sha256_text(trusted_principal_semantic_json(self))
        object.__setattr__(self, "principal_fingerprint", digest)
        object.__setattr__(self, "principal_id", "auth0-trusted-principal-" + digest)


def trusted_principal_semantic_json(value: TrustedPrincipal) -> str:
    return _semantic_json(value, ("principal_fingerprint", "principal_id"))


def serialize_trusted_principal(value: TrustedPrincipal) -> str:
    _require(
        type(value) is TrustedPrincipal and replace(value) == value,
        "trusted_principal",
    )
    return canonical_json(
        {item.name: _primitive(getattr(value, item.name)) for item in fields(value)}
    )


def _restore(
    raw_semantic: str,
    fingerprint: str,
    record_id: str,
    *,
    expected_schema: str,
    expected_keys: set[str],
    expected_type: type,
    fingerprint_name: str,
    id_name: str,
):
    try:
        document = json.loads(raw_semantic)
        _require(
            type(document) is dict and canonical_json(document) == raw_semantic,
            "canonical_semantic_json",
        )
        _require(set(document) == expected_keys, "canonical_semantic_json")
        _require(document.pop("schema_version") == expected_schema, "schema_version")
        _require(document.pop("rule_version") == AUTHORITY_RULE_VERSION, "rule_version")
        if expected_type is CompanyAuthorityRoot:
            _require(
                document.pop("provisioning_source")
                == ProvisioningSource.DEPLOYMENT_BOOTSTRAP.value,
                "provisioning_source",
            )
            _require(
                document.pop("worker_registry_schema_version")
                == WORKER_REGISTRY_SCHEMA_VERSION,
                "worker_registry_schema_version",
            )
        else:
            document["principal_type"] = PrincipalType(document["principal_type"])
        value = expected_type(**document)
        _require(getattr(value, fingerprint_name) == fingerprint, fingerprint_name)
        _require(getattr(value, id_name) == record_id, id_name)
        semantic = (
            company_authority_root_semantic_json(value)
            if expected_type is CompanyAuthorityRoot
            else trusted_principal_semantic_json(value)
        )
        _require(semantic == raw_semantic, "canonical_semantic_json")
        return value
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        raise AuthorityStorageError(
            "INVALID_AUTHORITY_RECORD",
            "authority_root" if expected_type is CompanyAuthorityRoot else "trusted_principal",
        ) from error


def restore_company_authority_root(
    raw_semantic: str,
    fingerprint: str,
    authority_root_id: str,
) -> CompanyAuthorityRoot:
    return _restore(
        raw_semantic,
        fingerprint,
        authority_root_id,
        expected_schema=AUTHORITY_ROOT_SCHEMA_VERSION,
        expected_keys={
            "canonical_worker_registry_json",
            "company_id",
            "company_plan_id",
            "company_plan_provenance_reference",
            "owner_principal_subject_id",
            "provisioning_reference",
            "provisioning_source",
            "rule_version",
            "schema_version",
            "worker_registry_fingerprint",
            "worker_registry_revision",
            "worker_registry_schema_version",
        },
        expected_type=CompanyAuthorityRoot,
        fingerprint_name="authority_root_fingerprint",
        id_name="authority_root_id",
    )


def restore_trusted_principal(
    raw_semantic: str,
    fingerprint: str,
    principal_id: str,
) -> TrustedPrincipal:
    return _restore(
        raw_semantic,
        fingerprint,
        principal_id,
        expected_schema=TRUSTED_PRINCIPAL_SCHEMA_VERSION,
        expected_keys={
            "authority_root_fingerprint",
            "authority_root_id",
            "company_id",
            "company_plan_id",
            "principal_subject_id",
            "principal_type",
            "rule_version",
            "schema_version",
            "worker_id",
            "worker_registry_fingerprint",
            "worker_registry_revision",
        },
        expected_type=TrustedPrincipal,
        fingerprint_name="principal_fingerprint",
        id_name="principal_id",
    )


__all__ = [
    "AUTHORITY_ROOT_SCHEMA_VERSION",
    "AUTHORITY_RULE_VERSION",
    "AuthorityBindingError",
    "AuthorityConflictError",
    "AuthorityError",
    "AuthorityStorageError",
    "AuthorityValidationError",
    "CompanyAuthorityRoot",
    "PrincipalType",
    "ProvisioningSource",
    "TRUSTED_PRINCIPAL_SCHEMA_VERSION",
    "TrustedPrincipal",
    "company_authority_root_semantic_json",
    "restore_company_authority_root",
    "restore_trusted_principal",
    "serialize_company_authority_root",
    "serialize_trusted_principal",
    "trusted_principal_semantic_json",
]
