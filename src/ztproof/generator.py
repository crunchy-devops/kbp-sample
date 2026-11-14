"""The generation stage: one prompt in, one untrusted reply out.

This is the only module that holds an API key, and it never runs Lean.
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from typing import Protocol

from .prompt import Prompt

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"
MAX_TOKENS = 64000


@dataclass
class Generation:
    text: str
    model_requested: str
    model_served: str = ""
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)
    request_id: str | None = None
    seconds: float = 0.0
    error: str | None = None       # set when no usable reply came back
    fallback_ran: bool = False     # another model answered after a policy decline
    effort: str = ""

    def to_json(self) -> dict:
        return asdict(self)


class Generator(Protocol):
    model: str

    def generate(self, prompt: Prompt) -> Generation: ...


class AnthropicGenerator:
    """Claude through the Anthropic API. Credentials come from the environment."""

    def __init__(self, model: str | None = None, effort: str | None = None) -> None:
        import anthropic  # imported here so that verification never needs the SDK

        self._anthropic = anthropic
        self.model = model or os.environ.get("ZTPROOF_MODEL") or DEFAULT_MODEL
        self.effort = effort or os.environ.get("ZTPROOF_EFFORT") or DEFAULT_EFFORT
        self.client = anthropic.Anthropic(max_retries=4)

    def generate(self, prompt: Prompt) -> Generation:
        anthropic = self._anthropic
        result = Generation(text="", model_requested=self.model, effort=self.effort)
        start = time.monotonic()
        try:
            # Streamed, because a long proof with adaptive thinking can outlast an
            # ordinary HTTP timeout. If the model declines on policy grounds the API
            # may re-run the request on a fallback model; the audit record says so.
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=prompt.system,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": prompt.user}],
            ) as stream:
                message = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            result.error = f"authentication failed: {exc.message}"
        except anthropic.PermissionDeniedError as exc:
            result.error = f"permission denied: {exc.message}"
        except anthropic.NotFoundError as exc:
            result.error = f"model or endpoint not found: {exc.message}"
        except anthropic.BadRequestError as exc:
            result.error = f"bad request: {exc.message}"
        except anthropic.RateLimitError as exc:
            result.error = f"rate limited after retries: {exc.message}"
        except anthropic.APIStatusError as exc:
            result.error = f"API error {exc.status_code}: {exc.message}"
        except anthropic.APIConnectionError as exc:
            result.error = f"connection error: {exc}"
        else:
            result.text = "".join(b.text for b in message.content if b.type == "text")
            result.model_served = message.model
            result.stop_reason = message.stop_reason or ""
            result.request_id = getattr(message, "_request_id", None)
            usage = message.usage
            result.usage = {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            }
            result.fallback_ran = any(
                getattr(entry, "type", "") == "fallback_message"
                for entry in (getattr(usage, "iterations", None) or [])
            )
            if message.stop_reason == "refusal":
                details = getattr(message, "stop_details", None)
                result.error = f"the model declined (category: {getattr(details, 'category', None)})"
            elif message.stop_reason == "max_tokens":
                result.error = f"the reply was cut off at {MAX_TOKENS} output tokens"
        result.seconds = time.monotonic() - start
        return result
