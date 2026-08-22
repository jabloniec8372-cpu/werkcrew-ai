"""Environment-driven Amazon Bedrock provider construction for M4."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

import boto3
from strands.models import BedrockModel


class AgentConfigurationError(RuntimeError):
    """Raised before a paid request when live provider configuration is incomplete."""


@dataclass(frozen=True, slots=True)
class BedrockSettings:
    model_id: str
    region: str
    profile: str | None = None

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> BedrockSettings:
        source = os.environ if environment is None else environment
        model_id = source.get("WERKCREW_BEDROCK_MODEL_ID", "").strip()
        region = (
            source.get("WERKCREW_AWS_REGION", "").strip()
            or source.get("AWS_REGION", "").strip()
            or source.get("AWS_DEFAULT_REGION", "").strip()
        )
        profile = source.get("AWS_PROFILE", "").strip() or None
        missing = []
        if not model_id:
            missing.append("WERKCREW_BEDROCK_MODEL_ID")
        if not region:
            missing.append("WERKCREW_AWS_REGION (lub AWS_REGION/AWS_DEFAULT_REGION)")
        if missing:
            raise AgentConfigurationError(
                "Brak konfiguracji live Bedrock: " + ", ".join(missing) + "."
            )
        return cls(model_id=model_id, region=region, profile=profile)


def build_bedrock_model(
    settings: BedrockSettings,
    *,
    session_factory=boto3.Session,
) -> BedrockModel:
    session = session_factory(
        profile_name=settings.profile,
        region_name=settings.region,
    )
    if session.get_credentials() is None:
        raise AgentConfigurationError(
            "Brak AWS credentials dla skonfigurowanego profilu/sesji."
        )
    return BedrockModel(
        model_id=settings.model_id,
        boto_session=session,
        temperature=0.0,
        max_tokens=900,
    )


def build_live_bedrock_model() -> BedrockModel:
    return build_bedrock_model(BedrockSettings.from_environment())
