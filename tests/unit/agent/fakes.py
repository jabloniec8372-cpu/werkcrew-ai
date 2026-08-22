"""Test-only Strands model doubles. Never used by the running application."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from strands.models import Model


class ScriptedModel(Model):
    """Returns explicit tool-use events while exercising the real Strands loop."""

    def __init__(self, actions: list[tuple[str, str, dict[str, Any] | None]]) -> None:
        self.actions = list(actions)
        self.config: dict[str, Any] = {"model_id": "test-scripted-model"}
        self.stream_calls = 0

    def update_config(self, **model_config: Any) -> None:
        self.config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return dict(self.config)

    async def structured_output(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tool_specs: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        self.stream_calls += 1
        if not self.actions:
            raise AssertionError("Brak kolejnej odpowiedzi w ScriptedModel")
        kind, value, payload = self.actions.pop(0)
        yield {"messageStart": {"role": "assistant"}}
        if kind == "tool":
            tool_use_id = f"tool-{self.stream_calls}"
            yield {
                "contentBlockStart": {
                    "start": {
                        "toolUse": {
                            "toolUseId": tool_use_id,
                            "name": value,
                        }
                    }
                }
            }
            yield {
                "contentBlockDelta": {
                    "delta": {"toolUse": {"input": json.dumps(payload or {})}}
                }
            }
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
            return

        yield {"contentBlockDelta": {"delta": {"text": value}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}


class ErrorModel(ScriptedModel):
    def __init__(self) -> None:
        super().__init__([])

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        if False:
            yield {}
        raise RuntimeError("simulated Bedrock outage")


def first_agent_run_model() -> ScriptedModel:
    return ScriptedModel(
        [
            ("tool", "get_job_state", {}),
            ("tool", "assess_job", {}),
            ("tool", "prepare_site_visit", {}),
            ("text", "Oględziny przydzielone. Czekam na raport terenowy.", None),
        ]
    )


def resumed_agent_run_model() -> ScriptedModel:
    return ScriptedModel(
        [
            ("tool", "get_job_state", {}),
            ("tool", "get_site_visit_status", {}),
            ("tool", "validate_site_visit_report", {}),
            ("tool", "generate_crew_plans", {}),
            ("tool", "calculate_plan_quotes", {}),
            ("text", "Dwa wycenione warianty są gotowe do przeglądu właściciela.", None),
        ]
    )


def pricing_from_ready_model() -> ScriptedModel:
    return ScriptedModel(
        [
            ("tool", "get_job_state", {}),
            ("tool", "generate_crew_plans", {}),
            ("tool", "calculate_plan_quotes", {}),
            ("text", "Wyceny są gotowe; właściciel wybiera wariant.", None),
        ]
    )


def calculate_pricing_model() -> ScriptedModel:
    return ScriptedModel(
        [
            ("tool", "get_job_state", {}),
            ("tool", "calculate_plan_quotes", {}),
            ("text", "Pricing wymaga wskazanych danych wejściowych.", None),
        ]
    )
