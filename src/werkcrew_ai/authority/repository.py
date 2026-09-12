"""Privileged AUTH-0 bootstrap writes and strict read-only replay.

Access to ``DeploymentAuthorityBootstrapRepository`` is the explicit v1 trust
anchor and must be restricted to deployment/bootstrap code.  No request route
constructs it.  ``TrustedAuthorityRepository`` is the runtime read boundary.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from werkcrew_ai.authority.models import (
    AuthorityBindingError,
    AuthorityConflictError,
    AuthorityStorageError,
    AuthorityValidationError,
    CompanyAuthorityRoot,
    PrincipalType,
    TrustedPrincipal,
    company_authority_root_semantic_json,
    restore_company_authority_root,
    restore_trusted_principal,
    trusted_principal_semantic_json,
)
from werkcrew_ai.field.repository import M2DurableRepository, M2PersistenceError
from werkcrew_ai.field.serialization import serialize_worker_registry, sha256_text
from werkcrew_ai.persistence import SqlitePersistence
from werkcrew_ai.planning.current_plan import CompanyPlan


@dataclass(frozen=True, slots=True)
class ProvisionedCompanyAuthority:
    authority_root: CompanyAuthorityRoot
    owner_principal: TrustedPrincipal


def _identity(value: str, field_name: str) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise AuthorityValidationError("INVALID_VALUE", field_name)


class TrustedAuthorityRepository:
    """Strict runtime reader; it cannot provision authority or principals."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(Path(database_path).expanduser().resolve())

    @contextmanager
    def _read_snapshot(self):
        try:
            connection = sqlite3.connect(
                Path(self.database_path).as_uri() + "?mode=ro",
                uri=True,
            )
        except sqlite3.Error as error:
            raise AuthorityStorageError("READ_UNAVAILABLE", "database") from error
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            yield connection
        except AuthorityStorageError:
            raise
        except sqlite3.Error as error:
            raise AuthorityStorageError("READ_INVALID", "database") from error
        finally:
            connection.rollback()
            connection.close()

    @staticmethod
    def _root_record(row) -> CompanyAuthorityRoot:
        value = restore_company_authority_root(
            row["canonical_semantic_json"],
            row["authority_root_fingerprint"],
            row["authority_root_id"],
        )
        actual = (
            row["schema_version"],
            row["rule_version"],
            row["company_id"],
            row["company_plan_id"],
            row["company_plan_provenance_reference"],
            row["owner_principal_subject_id"],
            row["provisioning_source"],
            row["provisioning_reference"],
            row["worker_registry_schema_version"],
            row["worker_registry_revision"],
            row["worker_registry_fingerprint"],
            row["canonical_worker_registry_json"],
        )
        expected = (
            value.schema_version,
            value.rule_version,
            value.company_id,
            value.company_plan_id,
            value.company_plan_provenance_reference,
            value.owner_principal_subject_id,
            value.provisioning_source.value,
            value.provisioning_reference,
            value.worker_registry_schema_version,
            value.worker_registry_revision,
            value.worker_registry_fingerprint,
            value.canonical_worker_registry_json,
        )
        if actual != expected:
            raise AuthorityStorageError(
                "AUTHORITY_ROOT_METADATA_MISMATCH", "authority_root"
            )
        return value

    @staticmethod
    def _principal_record(row) -> TrustedPrincipal:
        value = restore_trusted_principal(
            row["canonical_semantic_json"],
            row["principal_fingerprint"],
            row["principal_id"],
        )
        actual = (
            row["schema_version"],
            row["rule_version"],
            row["authority_root_id"],
            row["authority_root_fingerprint"],
            row["company_id"],
            row["company_plan_id"],
            row["principal_subject_id"],
            row["principal_type"],
            row["worker_id"],
            row["worker_registry_revision"],
            row["worker_registry_fingerprint"],
        )
        expected = (
            value.schema_version,
            value.rule_version,
            value.authority_root_id,
            value.authority_root_fingerprint,
            value.company_id,
            value.company_plan_id,
            value.principal_subject_id,
            value.principal_type.value,
            value.worker_id,
            value.worker_registry_revision,
            value.worker_registry_fingerprint,
        )
        if actual != expected:
            raise AuthorityStorageError(
                "TRUSTED_PRINCIPAL_METADATA_MISMATCH", "trusted_principal"
            )
        return value

    @staticmethod
    def _company_binding(connection, value: CompanyAuthorityRoot) -> None:
        row = connection.execute(
            "SELECT * FROM m3_company_plans WHERE company_plan_id=?",
            (value.company_plan_id,),
        ).fetchone()
        if row is None:
            raise AuthorityStorageError("COMPANY_PLAN_NOT_FOUND", "company_plan_id")
        company = CompanyPlan(
            company_plan_id=row["company_plan_id"],
            provenance_reference=row["provenance_reference"],
        )
        if (
            company.company_plan_id,
            company.provenance_reference,
        ) != (
            value.company_plan_id,
            value.company_plan_provenance_reference,
        ):
            raise AuthorityStorageError(
                "COMPANY_PLAN_BINDING_MISMATCH", "company_plan_id"
            )

    def _worker_registry_binding(
        self,
        connection,
        value: CompanyAuthorityRoot,
    ) -> None:
        try:
            current = M2DurableRepository(
                self.database_path
            )._worker_registry_from_connection(connection)
        except (M2PersistenceError, ValueError) as error:
            raise AuthorityStorageError(
                "WORKER_REGISTRY_INVALID", "worker_registry"
            ) from error
        if not set(value.worker_ids).issubset(current.worker_ids):
            raise AuthorityStorageError(
                "WORKER_REGISTRY_BINDING_MISMATCH", "worker_registry"
            )

    @staticmethod
    def _principal_binding(
        connection,
        root: CompanyAuthorityRoot,
        principal: TrustedPrincipal,
    ) -> None:
        if (
            principal.authority_root_id,
            principal.authority_root_fingerprint,
            principal.company_id,
            principal.company_plan_id,
            principal.worker_registry_revision,
            principal.worker_registry_fingerprint,
        ) != (
            root.authority_root_id,
            root.authority_root_fingerprint,
            root.company_id,
            root.company_plan_id,
            root.worker_registry_revision,
            root.worker_registry_fingerprint,
        ):
            raise AuthorityStorageError(
                "PRINCIPAL_AUTHORITY_ROOT_MISMATCH", "trusted_principal"
            )
        if principal.principal_type is PrincipalType.OWNER:
            if (
                principal.principal_subject_id != root.owner_principal_subject_id
                or principal.worker_id is not None
            ):
                raise AuthorityStorageError(
                    "OWNER_PRINCIPAL_BINDING_MISMATCH", "trusted_principal"
                )
            return
        if principal.worker_id not in root.worker_ids:
            raise AuthorityStorageError(
                "WORKER_REGISTRY_BINDING_MISMATCH", "worker_id"
            )
        row = connection.execute(
            "SELECT 1 FROM m2_worker_identities WHERE worker_id=?",
            (principal.worker_id,),
        ).fetchone()
        if row is None:
            raise AuthorityStorageError("WORKER_IDENTITY_NOT_FOUND", "worker_id")

    def _load_root(self, connection, authority_root_id: str) -> CompanyAuthorityRoot:
        row = connection.execute(
            "SELECT * FROM auth0_company_authority_roots WHERE authority_root_id=?",
            (authority_root_id,),
        ).fetchone()
        if row is None:
            raise AuthorityStorageError("AUTHORITY_ROOT_NOT_FOUND", "authority_root_id")
        value = self._root_record(row)
        self._company_binding(connection, value)
        self._worker_registry_binding(connection, value)
        owners = connection.execute(
            """
            SELECT * FROM auth0_trusted_principals
            WHERE authority_root_id=? AND principal_type='OWNER'
            """,
            (value.authority_root_id,),
        ).fetchall()
        if len(owners) != 1:
            raise AuthorityStorageError(
                "OWNER_PRINCIPAL_CARDINALITY_INVALID", "authority_root_id"
            )
        owner = self._principal_record(owners[0])
        self._principal_binding(connection, value, owner)
        return value

    def get_authority_root(self, authority_root_id: str) -> CompanyAuthorityRoot:
        _identity(authority_root_id, "authority_root_id")
        with self._read_snapshot() as connection:
            return self._load_root(connection, authority_root_id)

    def get_principal(self, principal_id: str) -> TrustedPrincipal:
        _identity(principal_id, "principal_id")
        with self._read_snapshot() as connection:
            row = connection.execute(
                "SELECT * FROM auth0_trusted_principals WHERE principal_id=?",
                (principal_id,),
            ).fetchone()
            if row is None:
                raise AuthorityStorageError("PRINCIPAL_NOT_FOUND", "principal_id")
            value = self._principal_record(row)
            root = self._load_root(connection, value.authority_root_id)
            self._principal_binding(connection, root, value)
            return value

    def resolve_principal(
        self,
        authority_root_id: str,
        principal_id: str,
    ) -> TrustedPrincipal:
        root = self.get_authority_root(authority_root_id)
        principal = self.get_principal(principal_id)
        if principal.authority_root_id != root.authority_root_id:
            raise AuthorityBindingError(
                "CROSS_COMPANY_PRINCIPAL_SUBSTITUTION", "principal_id"
            )
        return principal

    def resolve_worker_principal(
        self,
        authority_root_id: str,
        principal_id: str,
        worker_id: str,
    ) -> TrustedPrincipal:
        _identity(worker_id, "worker_id")
        principal = self.resolve_principal(authority_root_id, principal_id)
        if (
            principal.principal_type is not PrincipalType.WORKER
            or principal.worker_id != worker_id
        ):
            raise AuthorityBindingError(
                "WORKER_PRINCIPAL_SUBSTITUTION", "worker_id"
            )
        return principal


class DeploymentAuthorityBootstrapRepository(TrustedAuthorityRepository):
    """Deployment-only writer: possession by the host is the AUTH-0 trust anchor."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        migrations_directory: str | Path | None = None,
    ) -> None:
        super().__init__(database_path)
        keyword = (
            {}
            if migrations_directory is None
            else {"migrations_directory": migrations_directory}
        )
        self._persistence = SqlitePersistence(self.database_path, **keyword)

    def initialize(self, *, now: datetime) -> tuple[str, ...]:
        """Apply schema migrations from the deployment-only bootstrap boundary."""

        return self._persistence.initialize(now=now)

    @staticmethod
    def _insert_root(connection, value: CompanyAuthorityRoot) -> None:
        connection.execute(
            """
            INSERT INTO auth0_company_authority_roots(
                authority_root_id, authority_root_fingerprint,
                schema_version, rule_version, company_id, company_plan_id,
                company_plan_provenance_reference,
                owner_principal_subject_id, provisioning_source,
                provisioning_reference, worker_registry_schema_version,
                worker_registry_revision, worker_registry_fingerprint,
                canonical_worker_registry_json, canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                value.authority_root_id,
                value.authority_root_fingerprint,
                value.schema_version,
                value.rule_version,
                value.company_id,
                value.company_plan_id,
                value.company_plan_provenance_reference,
                value.owner_principal_subject_id,
                value.provisioning_source.value,
                value.provisioning_reference,
                value.worker_registry_schema_version,
                value.worker_registry_revision,
                value.worker_registry_fingerprint,
                value.canonical_worker_registry_json,
                company_authority_root_semantic_json(value),
            ),
        )

    @staticmethod
    def _insert_principal(connection, value: TrustedPrincipal) -> None:
        connection.execute(
            """
            INSERT INTO auth0_trusted_principals(
                principal_id, principal_fingerprint, schema_version,
                rule_version, authority_root_id, authority_root_fingerprint,
                company_id, company_plan_id, principal_subject_id,
                principal_type, worker_id, worker_registry_revision,
                worker_registry_fingerprint, canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                value.principal_id,
                value.principal_fingerprint,
                value.schema_version,
                value.rule_version,
                value.authority_root_id,
                value.authority_root_fingerprint,
                value.company_id,
                value.company_plan_id,
                value.principal_subject_id,
                value.principal_type.value,
                value.worker_id,
                value.worker_registry_revision,
                value.worker_registry_fingerprint,
                trusted_principal_semantic_json(value),
            ),
        )

    def provision_company_authority_from_deployment(
        self,
        *,
        company_id: str,
        company_plan_id: str,
        owner_principal_subject_id: str,
        provisioning_reference: str,
    ) -> ProvisionedCompanyAuthority:
        """Atomically provision one root and its sole OWNER from deployment input."""

        for value, name in (
            (company_id, "company_id"),
            (company_plan_id, "company_plan_id"),
            (owner_principal_subject_id, "owner_principal_subject_id"),
            (provisioning_reference, "provisioning_reference"),
        ):
            _identity(value, name)
        try:
            with self._persistence.transaction() as connection:
                company_row = connection.execute(
                    "SELECT * FROM m3_company_plans WHERE company_plan_id=?",
                    (company_plan_id,),
                ).fetchone()
                if company_row is None:
                    raise AuthorityBindingError(
                        "COMPANY_PLAN_NOT_FOUND", "company_plan_id"
                    )
                try:
                    registry = M2DurableRepository(
                        self.database_path
                    )._worker_registry_from_connection(connection)
                except (M2PersistenceError, ValueError) as error:
                    raise AuthorityBindingError(
                        "WORKER_REGISTRY_INVALID", "worker_registry"
                    ) from error
                registry_row = connection.execute(
                    "SELECT * FROM m2_worker_registry WHERE registry_key='GLOBAL'"
                ).fetchone()
                if registry_row is None:
                    raise AuthorityBindingError(
                        "WORKER_REGISTRY_NOT_FOUND", "worker_registry"
                    )
                raw_registry = serialize_worker_registry(registry)
                registry_fingerprint = sha256_text(raw_registry)
                if (
                    registry_row["root_schema_version"],
                    registry_row["registry_revision"],
                    registry_row["canonical_root_json"],
                    registry_row["content_sha256"],
                ) != (
                    "m2-worker-registry-v1",
                    registry.registry_revision,
                    raw_registry,
                    registry_fingerprint,
                ):
                    raise AuthorityBindingError(
                        "WORKER_REGISTRY_METADATA_MISMATCH", "worker_registry"
                    )
                root = CompanyAuthorityRoot(
                    company_id=company_id,
                    company_plan_id=company_plan_id,
                    company_plan_provenance_reference=company_row[
                        "provenance_reference"
                    ],
                    owner_principal_subject_id=owner_principal_subject_id,
                    provisioning_reference=provisioning_reference,
                    worker_registry_revision=registry.registry_revision,
                    worker_registry_fingerprint=registry_fingerprint,
                    canonical_worker_registry_json=raw_registry,
                )
                owner = TrustedPrincipal(
                    authority_root_id=root.authority_root_id,
                    authority_root_fingerprint=root.authority_root_fingerprint,
                    company_id=root.company_id,
                    company_plan_id=root.company_plan_id,
                    principal_subject_id=root.owner_principal_subject_id,
                    principal_type=PrincipalType.OWNER,
                    worker_id=None,
                    worker_registry_revision=root.worker_registry_revision,
                    worker_registry_fingerprint=root.worker_registry_fingerprint,
                )
                existing = connection.execute(
                    """
                    SELECT * FROM auth0_company_authority_roots
                    WHERE company_plan_id=?
                    """,
                    (company_plan_id,),
                ).fetchone()
                if existing is not None:
                    stored_root = self._root_record(existing)
                    owner_row = connection.execute(
                        """
                        SELECT * FROM auth0_trusted_principals
                        WHERE authority_root_id=? AND principal_type='OWNER'
                        """,
                        (stored_root.authority_root_id,),
                    ).fetchone()
                    if owner_row is None:
                        raise AuthorityStorageError(
                            "OWNER_PRINCIPAL_MISSING", "authority_root_id"
                        )
                    stored_owner = self._principal_record(owner_row)
                    if stored_root != root or stored_owner != owner:
                        raise AuthorityConflictError(
                            "AUTHORITY_ROOT_REPLAY_CONFLICT", "company_plan_id"
                        )
                    return ProvisionedCompanyAuthority(stored_root, stored_owner)
                self._insert_root(connection, root)
                self._insert_principal(connection, owner)
                return ProvisionedCompanyAuthority(root, owner)
        except sqlite3.IntegrityError as error:
            raise AuthorityConflictError(
                "AUTHORITY_PROVISIONING_CONFLICT", "authority_root"
            ) from error

    def provision_worker_principal_from_deployment(
        self,
        *,
        authority_root_id: str,
        principal_subject_id: str,
        worker_id: str,
    ) -> TrustedPrincipal:
        """Provision a fixed WORKER binding; this API has no caller-selected role."""

        for value, name in (
            (authority_root_id, "authority_root_id"),
            (principal_subject_id, "principal_subject_id"),
            (worker_id, "worker_id"),
        ):
            _identity(value, name)
        try:
            with self._persistence.transaction() as connection:
                root = self._load_root(connection, authority_root_id)
                if worker_id not in root.worker_ids:
                    raise AuthorityBindingError(
                        "WORKER_NOT_IN_AUTHORITY_REGISTRY", "worker_id"
                    )
                principal = TrustedPrincipal(
                    authority_root_id=root.authority_root_id,
                    authority_root_fingerprint=root.authority_root_fingerprint,
                    company_id=root.company_id,
                    company_plan_id=root.company_plan_id,
                    principal_subject_id=principal_subject_id,
                    principal_type=PrincipalType.WORKER,
                    worker_id=worker_id,
                    worker_registry_revision=root.worker_registry_revision,
                    worker_registry_fingerprint=root.worker_registry_fingerprint,
                )
                existing = connection.execute(
                    """
                    SELECT * FROM auth0_trusted_principals
                    WHERE authority_root_id=? AND principal_subject_id=?
                    """,
                    (authority_root_id, principal_subject_id),
                ).fetchone()
                if existing is not None:
                    stored = self._principal_record(existing)
                    if stored != principal:
                        raise AuthorityConflictError(
                            "PRINCIPAL_REPLAY_CONFLICT", "principal_subject_id"
                        )
                    return stored
                self._insert_principal(connection, principal)
                return principal
        except sqlite3.IntegrityError as error:
            raise AuthorityConflictError(
                "PRINCIPAL_PROVISIONING_CONFLICT", "trusted_principal"
            ) from error


__all__ = [
    "DeploymentAuthorityBootstrapRepository",
    "ProvisionedCompanyAuthority",
    "TrustedAuthorityRepository",
]
