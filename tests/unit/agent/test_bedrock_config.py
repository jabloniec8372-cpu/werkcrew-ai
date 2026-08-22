import pytest

from werkcrew_ai.agent import (
    AgentConfigurationError,
    BedrockSettings,
    build_bedrock_model,
)


def test_missing_model_and_region_are_reported_before_provider_call() -> None:
    with pytest.raises(AgentConfigurationError, match="WERKCREW_BEDROCK_MODEL_ID"):
        BedrockSettings.from_environment({})


def test_environment_settings_do_not_invent_model_or_region() -> None:
    settings = BedrockSettings.from_environment(
        {
            "WERKCREW_BEDROCK_MODEL_ID": "actual-account-model-id",
            "WERKCREW_AWS_REGION": "eu-central-1",
            "AWS_PROFILE": "werkcrew-demo",
        }
    )

    assert settings.model_id == "actual-account-model-id"
    assert settings.region == "eu-central-1"
    assert settings.profile == "werkcrew-demo"


def test_missing_credentials_fail_without_bedrock_request() -> None:
    class SessionWithoutCredentials:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def get_credentials(self):
            return None

    settings = BedrockSettings("model-id", "eu-central-1", "demo")

    with pytest.raises(AgentConfigurationError, match="Brak AWS credentials"):
        build_bedrock_model(settings, session_factory=SessionWithoutCredentials)


def test_bedrock_uses_region_from_session_without_duplicate_region_argument(
    monkeypatch,
) -> None:
    captured = {}

    class SessionWithCredentials:
        def __init__(self, **kwargs):
            captured["session_kwargs"] = kwargs

        def get_credentials(self):
            return object()

    def bedrock_model(**kwargs):
        captured["model_kwargs"] = kwargs
        return object()

    monkeypatch.setattr("werkcrew_ai.agent.bedrock.BedrockModel", bedrock_model)
    settings = BedrockSettings("model-id", "eu-central-1", "demo")

    build_bedrock_model(settings, session_factory=SessionWithCredentials)

    assert captured["session_kwargs"] == {
        "profile_name": "demo",
        "region_name": "eu-central-1",
    }
    assert captured["model_kwargs"]["boto_session"].get_credentials() is not None
    assert "region_name" not in captured["model_kwargs"]
