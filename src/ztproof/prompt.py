"""The prompt stage: from a pinned step to the exact text sent to a model.

Templates live in `prompts/<version>/` as plain Markdown, so a change of wording is
a reviewed diff and every audit record can name the version it used.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .lean import REPO_ROOT
from .problem import Problem, Step

PROMPT_VERSION = "v1"
PROMPTS_DIR = REPO_ROOT / "prompts"
MAX_FEEDBACK_CHARS = 6000

_FENCE = re.compile(r"^```[ \t]*(lean4?)[ \t]*\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL | re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


@dataclass
class Previous:
    """What the last attempt on this step looked like, for the repair prompt."""
    attempt: int
    body: str
    outcome: str
    reason: str
    diagnostics: list[str]


@dataclass
class Prompt:
    version: str
    system: str
    user: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256((self.system + "\0" + self.user).encode()).hexdigest()


def _template(version: str, name: str) -> str:
    return (PROMPTS_DIR / version / f"{name}.md").read_text(encoding="utf-8")


def _fill(template: str, values: dict[str, str]) -> str:
    def sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"prompt template uses unknown placeholder {{{{{key}}}}}")
        return values[key]
    # One pass only: text substituted in is never scanned for placeholders again.
    return _PLACEHOLDER.sub(sub, template)


def _clip(text: str, limit: int = MAX_FEEDBACK_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... ({len(text) - limit} more characters not shown)"


def build_prompt(
    problem: Problem, step: Step, *, attempt: int, max_attempts: int, lean_version: str,
    libraries: str, previous: Previous | None = None, version: str = PROMPT_VERSION,
) -> Prompt:
    values = {
        "attempt": str(attempt),
        "max_attempts": str(max_attempts),
        "problem_title": problem.title,
        "problem_status": "a known result" if problem.status == "known" else "an open problem",
        "problem_informal": problem.informal,
        "step_part": f" {step.part}" if step.part else "",
        "step_informal": step.informal,
        "lean_version": lean_version,
        "libraries": libraries,
        "preamble": ("import ZtPreamble  -- which is:\n" + problem.preamble_source()).rstrip()
        if problem.preamble_source().strip() else "-- no imports: Lean's core library only",
        "theorem": step.theorem or "",
        "signature": step.signature or "",
    }
    user = _fill(_template(version, "prove"), values)
    if previous is not None:
        user += _fill(_template(version, "repair"), {
            "previous_attempt": str(previous.attempt),
            "previous_body": previous.body.rstrip() or "(no Lean code could be extracted from the reply)",
            "previous_outcome": previous.outcome,
            "previous_reason": previous.reason,
            "previous_diagnostics": _clip("\n".join(previous.diagnostics)) or "(none)",
        })
    return Prompt(version=version, system=_template(version, "system").strip(), user=user)


class ProtocolViolation(ValueError):
    """The reply did not follow the output format. Counted as a failed attempt."""


def extract_lean(reply: str) -> str:
    """Return the body of the one `lean` code block in `reply`. Exactly one, or it fails."""
    blocks = _FENCE.findall(reply)
    if not blocks:
        raise ProtocolViolation("the reply contains no fenced `lean` code block")
    if len(blocks) > 1:
        raise ProtocolViolation(f"the reply contains {len(blocks)} `lean` code blocks; exactly one is allowed")
    return blocks[0][1]
