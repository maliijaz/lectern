"""Riding out a flaky model backend.

A question paper is roughly twenty sequential model calls. When Ollama's runner crashed at
call thirteen of a real forty-minute run, everything generated up to that point was lost.
These tests cover the fix: retry what a retry can fix, and fail fast on what it cannot.
"""

from __future__ import annotations

import pytest

from app.core.errors import LLMError, LLMUnavailable
from app.llm import structured
from app.llm.base import Completion, LLMProvider, user


class FlakyProvider(LLMProvider):
    """Fails a set number of times, then succeeds."""

    name = "flaky"

    def __init__(self, failures: list[Exception], reply: str = '{"ok": true}') -> None:
        super().__init__(base_url="memory://flaky", model="flaky")
        self.failures = list(failures)
        self.reply = reply
        self.calls = 0

    async def chat(self, messages, **_kwargs) -> Completion:  # noqa: ANN001, ANN003
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return Completion(text=self.reply, model=self.model)

    async def stream(self, messages, **_kwargs):  # noqa: ANN001, ANN003
        yield self.reply

    async def list_models(self) -> list[str]:
        return [self.model]


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """Retry backoff is real seconds in production; tests should not spend them."""

    async def instant(_delay: float) -> None:
        return None

    monkeypatch.setattr(structured.asyncio, "sleep", instant)


def _crash() -> LLMError:
    """The exact failure seen in the live run: Ollama's runner dying mid-job."""
    return LLMError(
        'Ollama returned 500: {"error":"an error was encountered while running the '
        'model: wsarecv: An existing connection was forcibly closed by the remote host."}',
        transient=True,
    )


async def test_a_crashed_runner_is_retried_and_succeeds() -> None:
    provider = FlakyProvider([_crash(), _crash()])
    result = await structured.call_with_retry(provider, [user("hello")])

    assert result.text == '{"ok": true}'
    assert provider.calls == 3, "it should have retried twice before succeeding"


async def test_a_permanent_failure_is_not_retried() -> None:
    """Waiting and asking again will not pull a model that was never installed."""
    provider = FlakyProvider([LLMError("Ollama does not have the model 'qwen9:80b'.")])

    with pytest.raises(LLMError, match="does not have the model"):
        await structured.call_with_retry(provider, [user("hello")])

    assert provider.calls == 1


async def test_the_retry_budget_is_finite() -> None:
    provider = FlakyProvider([_crash() for _ in range(10)])

    with pytest.raises(LLMError):
        await structured.call_with_retry(provider, [user("hello")], retries=2)

    assert provider.calls == 3  # the first attempt plus two retries


async def test_an_unreachable_backend_is_treated_as_transient() -> None:
    """Ollama restarting is the common case, and it comes back within seconds."""
    provider = FlakyProvider([LLMUnavailable("Cannot reach Ollama.", transient=True)])
    await structured.call_with_retry(provider, [user("hello")])
    assert provider.calls == 2


async def test_progress_is_reported_between_retries() -> None:
    """A teacher watching a progress bar deserves to know why it paused."""
    seen: list[tuple[int, str]] = []

    async def on_retry(attempt: int, message: str) -> None:
        seen.append((attempt, message))

    provider = FlakyProvider([_crash()])
    await structured.call_with_retry(provider, [user("hi")], on_retry=on_retry)

    assert len(seen) == 1
    assert seen[0][0] == 1
    assert "500" in seen[0][1]


async def test_structured_generation_survives_a_transient_failure() -> None:
    """The retry has to sit inside the repair loop, not around it."""
    from pydantic import BaseModel

    class Answer(BaseModel):
        value: str

    provider = FlakyProvider([_crash()], reply='{"value": "recovered"}')
    result = await structured.generate_structured(provider, Answer, [user("go")])

    assert result.value == "recovered"
    assert provider.calls == 2


def test_errors_default_to_permanent() -> None:
    """Retrying by default would mask real misconfiguration behind a long wait."""
    assert LLMError("something went wrong").transient is False
