"""Configuration and construction of persisted local Strands sessions for M6."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from strands.session.file_session_manager import FileSessionManager

from werkcrew_ai.agent.bedrock import AgentConfigurationError


WERKCREW_AGENT_ID = "werkcrew-coordinator"


def _default_session_storage(source: Mapping[str, str]) -> Path:
    local_app_data = source.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "WERKcrew_AI" / "strands-sessions"

    xdg_data_home = source.get("XDG_DATA_HOME", "").strip()
    if xdg_data_home:
        return Path(xdg_data_home) / "WERKcrew_AI" / "strands-sessions"

    return Path.home() / ".local" / "share" / "WERKcrew_AI" / "strands-sessions"


@dataclass(frozen=True, slots=True)
class StrandsSessionSettings:
    storage_dir: str

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> StrandsSessionSettings:
        source = os.environ if environment is None else environment
        configured = source.get("WERKCREW_STRANDS_SESSION_DIR", "").strip()
        storage = Path(configured) if configured else _default_session_storage(source)
        return cls(storage_dir=str(storage.resolve()))


def workflow_session_id(job_request_id: str, workflow_instance_id: str) -> str:
    if not job_request_id or not workflow_instance_id:
        raise AgentConfigurationError(
            "Session ID wymaga job_request_id i workflow_instance_id."
        )
    session_id = f"werkcrew-{job_request_id}-{workflow_instance_id}"
    if Path(session_id).name != session_id:
        raise AgentConfigurationError(
            "job_request_id/workflow_instance_id nie mogą zawierać separatora ścieżki."
        )
    return session_id


def build_file_session_manager(
    session_id: str,
    storage_dir: str,
) -> FileSessionManager:
    storage = Path(storage_dir).expanduser().resolve()
    try:
        storage.mkdir(parents=True, exist_ok=True)
        if not storage.is_dir():
            raise NotADirectoryError(str(storage))
        return FileSessionManager(session_id=session_id, storage_dir=str(storage))
    except OSError as exc:
        raise AgentConfigurationError(
            f"Nie można utworzyć zapisywalnego katalogu sesji Strands "
            f"'{storage}': {exc}"
        ) from exc
