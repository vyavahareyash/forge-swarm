"""Unit tests for AgyRunner headless parser and error conditions."""
import json

import pytest

from src.agents.agy_runner import (
    AgyNotInstalledError,
    AgyRunner,
)


def test_agy_runner_json_parsing():
    runner = AgyRunner(agy_path="/fake/agy")

    json_output = json.dumps({
        "response": "Here is the implementation plan.",
        "usage_metadata": {
            "prompt_token_count": 850,
            "candidates_token_count": 320,
            "thoughts_token_count": 150,
            "total_token_count": 1320,
        }
    })

    result = runner._parse_json_output(json_output)
    assert result.text == "Here is the implementation plan."
    assert result.prompt_tokens == 850
    assert result.candidates_tokens == 320
    assert result.thoughts_tokens == 150
    assert result.total_tokens == 1320
    assert result.usage_dict["total_token_count"] == 1320


def test_agy_runner_plaintext_fallback():
    runner = AgyRunner(agy_path="/fake/agy")
    plain_text = "Simple plain text answer from CLI."
    result = runner._parse_json_output(plain_text)

    assert result.text == plain_text
    assert result.total_tokens > 0


@pytest.mark.asyncio
async def test_agy_runner_missing_binary_raises():
    runner = AgyRunner(agy_path=None)
    with pytest.raises(AgyNotInstalledError) as exc_info:
        await runner.execute_turn("test prompt")
    assert "Antigravity CLI" in str(exc_info.value)
