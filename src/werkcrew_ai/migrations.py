"""Deterministic, fail-closed SQLite migration discovery and execution."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


_MIGRATION_FILE = re.compile(
    r"^(?P<version>[0-9]{4})_(?P<name>[a-z0-9]+(?:_[a-z0-9]+)*)\.sql$"
)
_LEGACY_HISTORY_COLUMNS = ("version", "applied_at")
_CURRENT_HISTORY_COLUMNS = (
    "migration_id",
    "version",
    "name",
    "checksum_sha256",
    "applied_at",
)
_FROZEN_MIGRATION_SHA256 = {
    "0001_m7_persistent_dispatch": (
        "2cbb90268d7a8ecd0ec7682e1265d70da2a700ca3399687bdc0b2c825e3af1fb"
    ),
    "0002_sequential_migration_history": (
        "bfac9d609bcad36c1aea365e4711f1b033eb75fcdaafe56c37b9797bd439d983"
    ),
    "0003_m1_canonical_job": (
        "2217984098e328d5fd611d273f38b87999991f0ffb294642631cb7e9eeb8ddb4"
    ),
    "0004_m1_lifecycle": (
        "f6a578e4dddfe5f7ce61bc66a4d2a68e4660529184465acfa7469417d2f65595"
    ),
    "0005_m1_boundary_publication": (
        "1a854baf6f19105126c856d15de2dbe078ac64894c08c77f9f677ebe9b35bd08"
    ),
    "0006_m2_durable_inbox": (
        "e6789b8412c456ae5a4ab7da9edff7be99ebab4e30fef1e324a2f57f7577a3af"
    ),
    "0007_m3_current_plan_bootstrap": (
        "fe0592a8eb04159d83dec884e5b90576898b5907c1db28226f9bf762a45ccdfa"
    ),
}


class MigrationError(RuntimeError):
    """Base error for migration discovery, validation, or execution."""


class MigrationDiscoveryError(MigrationError):
    """The migration set on disk is missing or not a contiguous sequence."""


class MigrationStateError(MigrationError):
    """The database history or schema cannot be reconciled safely."""


class MigrationApplyError(MigrationError):
    """A known pending migration failed and was rolled back."""


@dataclass(frozen=True, slots=True)
class Migration:
    migration_id: str
    version: int
    name: str
    checksum_sha256: str
    path: Path
    sql: str


def discover_migrations(directory: str | Path) -> tuple[Migration, ...]:
    """Return the complete numbered migration set in deterministic order."""

    root = Path(directory)
    if not root.is_dir():
        raise MigrationDiscoveryError(f"Migration directory is missing: {root}")

    migrations: list[Migration] = []
    for path in sorted(root.glob("*.sql"), key=lambda value: value.name):
        match = _MIGRATION_FILE.fullmatch(path.name)
        if match is None:
            raise MigrationDiscoveryError(
                f"Unsupported migration filename: {path.name}"
            )
        version = int(match.group("version"))
        migration_id = path.stem
        raw = path.read_bytes()
        migrations.append(
            Migration(
                migration_id=migration_id,
                version=version,
                name=match.group("name"),
                checksum_sha256=hashlib.sha256(raw).hexdigest(),
                path=path,
                sql=raw.decode("utf-8"),
            )
        )

    if not migrations:
        raise MigrationDiscoveryError(f"No migrations found in: {root}")
    migrations.sort(key=lambda item: item.version)
    versions = [item.version for item in migrations]
    expected = list(range(1, len(migrations) + 1))
    if versions != expected:
        raise MigrationDiscoveryError(
            f"Migration versions must be contiguous from 0001: {versions}"
        )
    if len({item.migration_id for item in migrations}) != len(migrations):
        raise MigrationDiscoveryError("Migration IDs must be unique")
    for migration_id, checksum in _FROZEN_MIGRATION_SHA256.items():
        matching = [
            item for item in migrations if item.migration_id == migration_id
        ]
        if len(matching) != 1 or matching[0].checksum_sha256 != checksum:
            raise MigrationDiscoveryError(
                f"Frozen migration does not match its checksum: {migration_id}"
            )
    return tuple(migrations)


class MigrationRunner:
    """Apply a known migration prefix atomically and reject ambiguous state."""

    def __init__(self, migrations_directory: str | Path) -> None:
        self._migrations_directory = Path(migrations_directory)

    @property
    def known_migrations(self) -> tuple[Migration, ...]:
        return discover_migrations(self._migrations_directory)

    def run(
        self,
        connection: sqlite3.Connection,
        *,
        applied_at: datetime,
    ) -> tuple[str, ...]:
        """Apply only pending migrations and return their exact IDs."""

        if applied_at.tzinfo is None or applied_at.utcoffset() is None:
            raise ValueError("applied_at must be timezone-aware")
        migrations = self.known_migrations
        applied = self._validated_applied_prefix(connection, migrations)
        newly_applied: list[str] = []

        for migration in migrations[len(applied) :]:
            try:
                connection.executescript("BEGIN IMMEDIATE;\n" + migration.sql)
                self._record_migration(connection, migration, applied_at)
                expected_prefix = migrations[: migration.version]
                self._validated_applied_prefix(connection, migrations)
                self._validate_schema(connection, expected_prefix)
                connection.commit()
            except MigrationError:
                if connection.in_transaction:
                    connection.rollback()
                raise
            except (OSError, UnicodeError, sqlite3.Error) as error:
                if connection.in_transaction:
                    connection.rollback()
                raise MigrationApplyError(
                    f"Failed to apply migration {migration.migration_id}"
                ) from error
            newly_applied.append(migration.migration_id)

        return tuple(newly_applied)

    def applied_migration_ids(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[str, ...]:
        """Return the validated applied prefix without changing the database."""

        migrations = self.known_migrations
        applied = self._validated_applied_prefix(connection, migrations)
        return tuple(item.migration_id for item in applied)

    def _validated_applied_prefix(
        self,
        connection: sqlite3.Connection,
        migrations: tuple[Migration, ...],
    ) -> tuple[Migration, ...]:
        objects = _schema_signature(connection)
        history_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if history_sql is None:
            if objects:
                raise MigrationStateError(
                    "Database has schema objects but no migration history"
                )
            return ()

        columns = tuple(
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(schema_migrations)"
            ).fetchall()
        )
        if columns == _LEGACY_HISTORY_COLUMNS:
            rows = connection.execute(
                "SELECT version, applied_at FROM schema_migrations ORDER BY version"
            ).fetchall()
            if [row[0] for row in rows] != [1]:
                raise MigrationStateError(
                    "Legacy migration history must contain exactly version 0001"
                )
            applied = migrations[:1]
        elif columns == _CURRENT_HISTORY_COLUMNS:
            rows = connection.execute(
                """
                SELECT migration_id, version, name, checksum_sha256, applied_at
                FROM schema_migrations
                ORDER BY version
                """
            ).fetchall()
            if not rows:
                raise MigrationStateError("Migration history is empty")
            versions = [row[1] for row in rows]
            if versions != list(range(1, len(rows) + 1)):
                raise MigrationStateError(
                    f"Applied migration versions are not contiguous: {versions}"
                )
            if len(rows) > len(migrations):
                raise MigrationStateError(
                    "Database contains migrations unknown to this application"
                )
            applied = migrations[: len(rows)]
            for row, migration in zip(rows, applied, strict=True):
                recorded = tuple(row[:4])
                expected = (
                    migration.migration_id,
                    migration.version,
                    migration.name,
                    migration.checksum_sha256,
                )
                if recorded != expected:
                    raise MigrationStateError(
                        "Migration history metadata does not match known files: "
                        f"{migration.migration_id}"
                    )
        else:
            raise MigrationStateError(
                f"Unsupported migration history schema: {columns}"
            )

        self._validate_schema(connection, applied)
        return applied

    @staticmethod
    def _record_migration(
        connection: sqlite3.Connection,
        migration: Migration,
        applied_at: datetime,
    ) -> None:
        columns = tuple(
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(schema_migrations)"
            ).fetchall()
        )
        if columns == _LEGACY_HISTORY_COLUMNS and migration.version == 1:
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES(?, ?)",
                (migration.version, applied_at.isoformat()),
            )
            return
        if columns != _CURRENT_HISTORY_COLUMNS:
            raise MigrationStateError(
                "Pending migration did not establish the expected history schema"
            )
        connection.execute(
            """
            INSERT INTO schema_migrations(
                migration_id, version, name, checksum_sha256, applied_at
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                migration.migration_id,
                migration.version,
                migration.name,
                migration.checksum_sha256,
                applied_at.isoformat(),
            ),
        )

    @staticmethod
    def _validate_schema(
        connection: sqlite3.Connection,
        applied: tuple[Migration, ...],
    ) -> None:
        expected = _expected_schema_signature(applied)
        actual = _schema_signature(connection)
        if actual != expected:
            raise MigrationStateError(
                "SQLite schema does not match its recorded migration history"
            )


def _expected_schema_signature(
    migrations: tuple[Migration, ...],
) -> tuple[tuple[str, str, str, str], ...]:
    if not migrations:
        return ()
    reference = sqlite3.connect(":memory:")
    try:
        reference.create_function(
            "werkcrew_canonical_json",
            1,
            lambda value: json.dumps(
                json.loads(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            if isinstance(value, str)
            else None,
            deterministic=True,
        )
        reference.create_function(
            "werkcrew_sha256",
            1,
            lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
            if isinstance(value, str)
            else None,
            deterministic=True,
        )
        reference.execute("PRAGMA foreign_keys = ON")
        for migration in migrations:
            reference.executescript(migration.sql)
        return _schema_signature(reference)
    finally:
        reference.close()


def _schema_signature(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, str, str], ...]:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL
        ORDER BY type, name
        """
    ).fetchall()
    return tuple(
        (row[0], row[1], row[2], " ".join(row[3].split())) for row in rows
    )
