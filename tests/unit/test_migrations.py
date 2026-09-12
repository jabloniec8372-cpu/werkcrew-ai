from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import werkcrew_ai.migrations as migration_module
from werkcrew_ai.migrations import (
    MigrationDiscoveryError,
    MigrationStateError,
    discover_migrations,
)
from werkcrew_ai.persistence import (
    MIGRATIONS_DIRECTORY,
    MIGRATION_PATH,
    SqliteBusinessRepository,
    SqlitePersistence,
)
from werkcrew_ai.planning.current_plan import PlanRevision, serialize_plan_revision


NOW = datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)
MIGRATION_IDS = (
    "0001_m7_persistent_dispatch",
    "0002_sequential_migration_history",
    "0003_m1_canonical_job",
    "0004_m1_lifecycle",
    "0005_m1_boundary_publication",
    "0006_m2_durable_inbox",
    "0007_m3_current_plan_bootstrap",
    "0008_m3_evaluation_input",
    "0009_m3_feasibility_support",
    "0010_m5_internal_labor_cost_support",
    "0011_auth0_trusted_principals",
)
FROZEN_0001_SHA256 = (
    "2cbb90268d7a8ecd0ec7682e1265d70da2a700ca3399687bdc0b2c825e3af1fb"
)
FROZEN_0002_SHA256 = (
    "bfac9d609bcad36c1aea365e4711f1b033eb75fcdaafe56c37b9797bd439d983"
)
FROZEN_0003_SHA256 = (
    "2217984098e328d5fd611d273f38b87999991f0ffb294642631cb7e9eeb8ddb4"
)
FROZEN_0004_SHA256 = (
    "f6a578e4dddfe5f7ce61bc66a4d2a68e4660529184465acfa7469417d2f65595"
)
FROZEN_0005_SHA256 = (
    "1a854baf6f19105126c856d15de2dbe078ac64894c08c77f9f677ebe9b35bd08"
)
FROZEN_0006_SHA256 = (
    "e6789b8412c456ae5a4ab7da9edff7be99ebab4e30fef1e324a2f57f7577a3af"
)
FROZEN_0007_SHA256 = (
    "fe0592a8eb04159d83dec884e5b90576898b5907c1db28226f9bf762a45ccdfa"
)
FROZEN_0008_SHA256 = (
    "735a09e3e478656b332d6bc361e88946d070f1057d848c691a79438bfd8a9a87"
)
FROZEN_0009_SHA256 = (
    "be3724e7949866936ad2dfc340eb7db043a354795b9e1445e580381a418a11a9"
)
FROZEN_0010_SHA256 = (
    "25a4758a1f225888a331e5a385641bb7b4136890ed1166cd9ab2fb6c67b69ba2"
)
FROZEN_0011_SHA256 = (
    "d444ab503ef7ee3521b15a3120707217da01854d99484dfa465fedacba593898"
)

M2_DUPLICATE_INSERT_GUARDS = (
    "m2_assignment_members_no_duplicate_insert",
    "m2_assignment_plan_days_no_duplicate_insert",
    "m2_assignment_routes_no_duplicate_insert",
    "m2_directive_roots_no_duplicate_insert",
    "m2_effect_outbox_no_duplicate_insert",
    "m2_evidence_identities_no_duplicate_insert",
    "m2_evidence_usages_no_duplicate_insert",
    "m2_input_conflicts_no_duplicate_insert",
    "m2_input_inbox_no_duplicate_insert",
    "m2_job_execution_roots_no_duplicate_insert",
    "m2_plan_day_roots_no_duplicate_insert",
    "m2_task_routes_no_duplicate_insert",
    "m2_worker_identities_no_duplicate_insert",
    "m2_worker_registry_no_duplicate_insert",
)
M5_TABLES_FOR_MIGRATION_TEST = (
    "m5_internal_cost_rules",
    "m5_internal_labor_rate_sources",
    "m5_internal_cost_source_captures",
    "m5_internal_cost_support_cuts",
    "m5_internal_cost_support_candidates",
    "m5_internal_cost_support_subjects",
    "m5_internal_cost_support_selections",
)
AUTH0_TABLES_FOR_MIGRATION_TEST = (
    "auth0_company_authority_roots",
    "auth0_trusted_principals",
)


def _history(database_path: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(database_path) as connection:
        return connection.execute(
            """
            SELECT migration_id, version, name, checksum_sha256, applied_at
            FROM schema_migrations
            ORDER BY version
            """
        ).fetchall()


def _initialize_legacy_0001(database_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(1, ?)",
            (NOW.isoformat(),),
        )
        connection.execute(
            """
            INSERT INTO jobs(
                job_id, title, address_status, address_json,
                created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                "gen1-existing-job",
                "Existing Gen1 job",
                "UNCONFIRMED",
                "{}",
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )


def test_fresh_database_runs_all_migrations_in_sequence(tmp_path: Path) -> None:
    database_path = tmp_path / "fresh.db"
    persistence = SqlitePersistence(database_path)

    assert persistence.initialize(now=NOW) == MIGRATION_IDS
    assert persistence.applied_migration_ids() == MIGRATION_IDS

    rows = _history(database_path)
    assert [row[0] for row in rows] == list(MIGRATION_IDS)
    assert [row[1] for row in rows] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]


def test_existing_0001_database_applies_only_later_migrations(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "existing.db"
    _initialize_legacy_0001(database_path)
    persistence = SqlitePersistence(database_path)

    assert persistence.initialize(now=NOW) == MIGRATION_IDS[1:]
    assert persistence.applied_migration_ids() == MIGRATION_IDS

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT title FROM jobs WHERE job_id='gen1-existing-job'"
        ).fetchone() == ("Existing Gen1 job",)


def test_second_initialize_has_zero_duplicate_effects(tmp_path: Path) -> None:
    database_path = tmp_path / "idempotent.db"
    persistence = SqlitePersistence(database_path)

    assert persistence.initialize(now=NOW) == MIGRATION_IDS
    first_history = _history(database_path)
    assert persistence.initialize(now=NOW) == ()
    assert _history(database_path) == first_history


def test_migration_history_persists_exact_ids_and_checksums(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "history.db"
    persistence = SqlitePersistence(database_path)
    migrations = discover_migrations(MIGRATIONS_DIRECTORY)

    persistence.initialize(now=NOW)

    assert _history(database_path) == [
        (
            migration.migration_id,
            migration.version,
            migration.name,
            migration.checksum_sha256,
            NOW.isoformat(),
        )
        for migration in migrations
    ]


def test_unknown_migration_history_fails_closed(tmp_path: Path) -> None:
    database_path = tmp_path / "unknown.db"
    persistence = SqlitePersistence(database_path)
    persistence.initialize(now=NOW)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO schema_migrations(
                migration_id, version, name, checksum_sha256, applied_at
            ) VALUES('0012_unknown', 12, 'unknown', ?, ?)
            """,
            ("0" * 64, NOW.isoformat()),
        )

    with pytest.raises(MigrationStateError, match="unknown"):
        persistence.initialize(now=NOW)


def test_inconsistent_schema_fails_closed(tmp_path: Path) -> None:
    database_path = tmp_path / "inconsistent.db"
    persistence = SqlitePersistence(database_path)
    persistence.initialize(now=NOW)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE agent_trace_events")

    with pytest.raises(MigrationStateError, match="schema"):
        persistence.initialize(now=NOW)


def test_existing_repository_uses_shared_sqlite_foundation(tmp_path: Path) -> None:
    repository = SqliteBusinessRepository(tmp_path / "business.db")

    assert isinstance(repository, SqlitePersistence)
    assert repository.initialize(now=NOW) == MIGRATION_IDS
    assert repository.applied_migration_ids() == MIGRATION_IDS


def test_migration_0001_remains_frozen() -> None:
    assert hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest() == (
        FROZEN_0001_SHA256
    )


def test_migration_0002_remains_frozen() -> None:
    migration_path = MIGRATIONS_DIRECTORY / "0002_sequential_migration_history.sql"
    assert hashlib.sha256(migration_path.read_bytes()).hexdigest() == (
        FROZEN_0002_SHA256
    )


def test_migration_0003_remains_frozen() -> None:
    migration_path = MIGRATIONS_DIRECTORY / "0003_m1_canonical_job.sql"
    assert hashlib.sha256(migration_path.read_bytes()).hexdigest() == (
        FROZEN_0003_SHA256
    )


def test_migration_0004_matches_frozen_lifecycle_contract() -> None:
    migration_path = MIGRATIONS_DIRECTORY / "0004_m1_lifecycle.sql"
    assert hashlib.sha256(migration_path.read_bytes()).hexdigest() == (
        FROZEN_0004_SHA256
    )


def test_migration_0005_matches_frozen_boundary_contract() -> None:
    migration_path = MIGRATIONS_DIRECTORY / "0005_m1_boundary_publication.sql"
    assert hashlib.sha256(migration_path.read_bytes()).hexdigest() == (
        FROZEN_0005_SHA256
    )


def test_migration_0006_matches_frozen_durable_m2_schema() -> None:
    migration_path = MIGRATIONS_DIRECTORY / "0006_m2_durable_inbox.sql"
    assert hashlib.sha256(migration_path.read_bytes()).hexdigest() == (
        FROZEN_0006_SHA256
    )


def test_migration_0007_matches_frozen_m3_current_plan_bootstrap() -> None:
    migration_path = MIGRATIONS_DIRECTORY / "0007_m3_current_plan_bootstrap.sql"
    assert hashlib.sha256(migration_path.read_bytes()).hexdigest() == (
        FROZEN_0007_SHA256
    )


def test_tampered_migration_0007_is_rejected_before_fresh_schema_application(
    tmp_path: Path,
) -> None:
    migration_directory = tmp_path / "tampered-migrations"
    migration_directory.mkdir()
    for source in MIGRATIONS_DIRECTORY.glob("*.sql"):
        (migration_directory / source.name).write_bytes(source.read_bytes())
    migration_path = migration_directory / "0007_m3_current_plan_bootstrap.sql"
    migration_path.write_bytes(migration_path.read_bytes() + b"\n-- adversarial change\n")
    database_path = tmp_path / "must-not-exist.db"

    with pytest.raises(MigrationDiscoveryError, match="0007_m3_current_plan_bootstrap"):
        SqlitePersistence(database_path, migrations_directory=migration_directory).initialize(now=NOW)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type IN ('table', 'trigger')"
        ).fetchone() == (0,)


def test_migration_0008_matches_frozen_m3_evaluation_input() -> None:
    migration_path = MIGRATIONS_DIRECTORY / "0008_m3_evaluation_input.sql"
    assert hashlib.sha256(migration_path.read_bytes()).hexdigest() == (
        FROZEN_0008_SHA256
    )


def test_tampered_migration_0008_is_rejected_before_fresh_schema_application(
    tmp_path: Path,
) -> None:
    migration_directory = tmp_path / "tampered-migrations"
    migration_directory.mkdir()
    for source in MIGRATIONS_DIRECTORY.glob("*.sql"):
        (migration_directory / source.name).write_bytes(source.read_bytes())
    migration_path = migration_directory / "0008_m3_evaluation_input.sql"
    migration_path.write_bytes(migration_path.read_bytes() + b"\n-- adversarial change\n")
    database_path = tmp_path / "must-not-exist.db"

    with pytest.raises(MigrationDiscoveryError, match="0008_m3_evaluation_input"):
        SqlitePersistence(database_path, migrations_directory=migration_directory).initialize(now=NOW)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type IN ('table', 'trigger')"
        ).fetchone() == (0,)


def test_migration_0009_matches_frozen_m3_feasibility_support() -> None:
    migration_path = MIGRATIONS_DIRECTORY / "0009_m3_feasibility_support.sql"
    assert hashlib.sha256(migration_path.read_bytes()).hexdigest() == (
        FROZEN_0009_SHA256
    )


def test_tampered_migration_0009_is_rejected_before_fresh_schema_application(
    tmp_path: Path,
) -> None:
    migration_directory = tmp_path / "tampered-migrations"
    migration_directory.mkdir()
    for source in MIGRATIONS_DIRECTORY.glob("*.sql"):
        (migration_directory / source.name).write_bytes(source.read_bytes())
    migration_path = migration_directory / "0009_m3_feasibility_support.sql"
    migration_path.write_bytes(migration_path.read_bytes() + b"\n-- adversarial change\n")
    database_path = tmp_path / "must-not-exist.db"

    with pytest.raises(MigrationDiscoveryError, match="0009_m3_feasibility_support"):
        SqlitePersistence(
            database_path, migrations_directory=migration_directory
        ).initialize(now=NOW)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type IN ('table', 'trigger')"
        ).fetchone() == (0,)


def test_exact_0008_database_upgrades_through_current_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pre_0009 = tmp_path / "migrations-through-0008"
    pre_0009.mkdir()
    for source in MIGRATIONS_DIRECTORY.glob("*.sql"):
        if source.name.startswith(("0009_", "0010_", "0011_")):
            continue
        (pre_0009 / source.name).write_bytes(source.read_bytes())
    monkeypatch.delitem(
        migration_module._FROZEN_MIGRATION_SHA256,
        "0009_m3_feasibility_support",
    )
    monkeypatch.delitem(
        migration_module._FROZEN_MIGRATION_SHA256,
        "0010_m5_internal_labor_cost_support",
    )
    monkeypatch.delitem(
        migration_module._FROZEN_MIGRATION_SHA256,
        "0011_auth0_trusted_principals",
    )
    database_path = tmp_path / "upgrade.db"
    SqlitePersistence(
        database_path, migrations_directory=pre_0009
    ).initialize(now=NOW)
    monkeypatch.setitem(
        migration_module._FROZEN_MIGRATION_SHA256,
        "0009_m3_feasibility_support",
        FROZEN_0009_SHA256,
    )
    monkeypatch.setitem(
        migration_module._FROZEN_MIGRATION_SHA256,
        "0010_m5_internal_labor_cost_support",
        FROZEN_0010_SHA256,
    )
    monkeypatch.setitem(
        migration_module._FROZEN_MIGRATION_SHA256,
        "0011_auth0_trusted_principals",
        FROZEN_0011_SHA256,
    )

    persistence = SqlitePersistence(database_path)
    assert persistence.initialize(now=NOW) == (
        "0009_m3_feasibility_support",
        "0010_m5_internal_labor_cost_support",
        "0011_auth0_trusted_principals",
    )
    assert persistence.initialize(now=NOW) == ()
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "m3_feasibility_m8_configurations",
        "m3_feasibility_worker_registry_provenance",
        "m3_feasibility_worker_registry_captures",
        "m3_feasibility_source_records",
        "m3_feasibility_source_selection_cuts",
        "m3_feasibility_support_snapshots",
        "m3_feasibility_support_source_bindings",
    } <= tables
    assert set(M5_TABLES_FOR_MIGRATION_TEST) <= tables
    assert set(AUTH0_TABLES_FOR_MIGRATION_TEST) <= tables


def test_migration_0010_matches_frozen_m5_internal_labor_cost_support() -> None:
    migration_path = MIGRATIONS_DIRECTORY / "0010_m5_internal_labor_cost_support.sql"
    assert hashlib.sha256(migration_path.read_bytes()).hexdigest() == (
        FROZEN_0010_SHA256
    )


def test_tampered_migration_0010_is_rejected_before_fresh_schema_application(
    tmp_path: Path,
) -> None:
    migration_directory = tmp_path / "tampered-migrations"
    migration_directory.mkdir()
    for source in MIGRATIONS_DIRECTORY.glob("*.sql"):
        (migration_directory / source.name).write_bytes(source.read_bytes())
    migration_path = migration_directory / "0010_m5_internal_labor_cost_support.sql"
    migration_path.write_bytes(migration_path.read_bytes() + b"\n-- adversarial change\n")
    database_path = tmp_path / "must-not-exist.db"

    with pytest.raises(MigrationDiscoveryError, match="0010_m5_internal_labor_cost_support"):
        SqlitePersistence(
            database_path, migrations_directory=migration_directory
        ).initialize(now=NOW)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type IN ('table', 'trigger')"
        ).fetchone() == (0,)


def test_migration_0011_matches_frozen_auth0_trusted_principals() -> None:
    migration_path = MIGRATIONS_DIRECTORY / "0011_auth0_trusted_principals.sql"
    assert hashlib.sha256(migration_path.read_bytes()).hexdigest() == (
        FROZEN_0011_SHA256
    )


def test_tampered_migration_0011_is_rejected_before_fresh_schema_application(
    tmp_path: Path,
) -> None:
    migration_directory = tmp_path / "tampered-migrations"
    migration_directory.mkdir()
    for source in MIGRATIONS_DIRECTORY.glob("*.sql"):
        (migration_directory / source.name).write_bytes(source.read_bytes())
    migration_path = migration_directory / "0011_auth0_trusted_principals.sql"
    migration_path.write_bytes(migration_path.read_bytes() + b"\n-- adversarial change\n")
    database_path = tmp_path / "must-not-exist.db"

    with pytest.raises(MigrationDiscoveryError, match="0011_auth0_trusted_principals"):
        SqlitePersistence(
            database_path, migrations_directory=migration_directory
        ).initialize(now=NOW)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type IN ('table', 'trigger')"
        ).fetchone() == (0,)


def test_exact_0009_database_upgrades_once_to_0010_and_preserves_m2_m3_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pre_0010 = tmp_path / "migrations-through-0009"
    pre_0010.mkdir()
    for source in MIGRATIONS_DIRECTORY.glob("*.sql"):
        if source.name.startswith(("0010_", "0011_")):
            continue
        (pre_0010 / source.name).write_bytes(source.read_bytes())
    monkeypatch.delitem(
        migration_module._FROZEN_MIGRATION_SHA256,
        "0010_m5_internal_labor_cost_support",
    )
    monkeypatch.delitem(
        migration_module._FROZEN_MIGRATION_SHA256,
        "0011_auth0_trusted_principals",
    )
    database_path = tmp_path / "upgrade.db"
    SqlitePersistence(
        database_path, migrations_directory=pre_0010
    ).initialize(now=NOW)

    revision = PlanRevision(
        company_plan_id="existing-company-plan",
        revision=0,
        previous_revision_id=None,
        provenance_reference="pre-0010-state",
        plan_days=(),
        commitments=(),
        dependencies=(),
    )
    raw_revision = serialize_plan_revision(revision)
    with sqlite3.connect(database_path) as connection:
        connection.create_function(
            "werkcrew_canonical_json",
            1,
            lambda value: value,
            deterministic=True,
        )
        connection.create_function(
            "werkcrew_sha256",
            1,
            lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest(),
            deterministic=True,
        )
        connection.execute(
            "INSERT INTO m3_company_plans VALUES(?, ?)",
            (revision.company_plan_id, "pre-0010-state"),
        )
        connection.execute(
            "INSERT INTO m3_plan_revisions VALUES(?,?,?,?,?,?)",
            (
                revision.company_plan_id,
                revision.revision,
                revision.revision_id,
                revision.previous_revision_id,
                revision.fingerprint,
                raw_revision,
            ),
        )
        connection.execute(
            "INSERT INTO m2_worker_registry VALUES('GLOBAL','m2-worker-registry-v1',0,?,?)",
            (
                '{"document_type":"WorkerIdentityRegistry","payload":{"registry_revision":0,"worker_ids":["existing-worker"]},"schema_version":"m2-worker-registry-v1"}',
                hashlib.sha256(
                    b'{"document_type":"WorkerIdentityRegistry","payload":{"registry_revision":0,"worker_ids":["existing-worker"]},"schema_version":"m2-worker-registry-v1"}'
                ).hexdigest(),
            ),
        )
        connection.execute(
            "INSERT INTO m2_worker_identities(worker_id) VALUES('existing-worker')"
        )

    monkeypatch.setitem(
        migration_module._FROZEN_MIGRATION_SHA256,
        "0010_m5_internal_labor_cost_support",
        FROZEN_0010_SHA256,
    )
    monkeypatch.setitem(
        migration_module._FROZEN_MIGRATION_SHA256,
        "0011_auth0_trusted_principals",
        FROZEN_0011_SHA256,
    )
    persistence = SqlitePersistence(database_path)
    assert persistence.initialize(now=NOW) == (
        "0010_m5_internal_labor_cost_support",
        "0011_auth0_trusted_principals",
    )
    assert persistence.initialize(now=NOW) == ()
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT provenance_reference FROM m3_company_plans WHERE company_plan_id='existing-company-plan'"
        ).fetchone() == ("pre-0010-state",)
        assert connection.execute(
            "SELECT worker_id FROM m2_worker_identities"
        ).fetchone() == ("existing-worker",)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert set(M5_TABLES_FOR_MIGRATION_TEST) <= tables
    assert set(AUTH0_TABLES_FOR_MIGRATION_TEST) <= tables


def test_migration_0006_installs_all_replace_independent_identity_guards(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "replace-guards.db"
    persistence = SqlitePersistence(database_path)
    persistence.initialize(now=NOW)

    with sqlite3.connect(database_path) as connection:
        guards = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='trigger' AND name LIKE 'm2_%_no_duplicate_insert'
                ORDER BY name
                """
            )
        )

    assert guards == M2_DUPLICATE_INSERT_GUARDS
