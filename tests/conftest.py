from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_strands_session_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep every test's real Strands session files isolated and disposable."""
    monkeypatch.setenv(
        "WERKCREW_STRANDS_SESSION_DIR",
        str(tmp_path / "strands-sessions"),
    )
