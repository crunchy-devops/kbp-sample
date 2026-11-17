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

PROMPT_VERSION = "v2"
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


def _bullets(items) -> str:
    return "\n".join(f"* {item}" for item in items) or "* (none)"


def _context_section(problem: Problem, step: Step, version: str) -> str:
    """The context pack, as the model sees it. Empty when the problem has none."""
    pack = problem.context
    if pack is None:
        return ""
    assumptions = [f"`{a.name}`: {a.stands_for} How to use it: {a.how_to_use}" for a in pack.assumptions]
    budget = []
    if pack.budget.max_seconds_per_build:
        budget.append(f"Lean is stopped after {pack.budget.max_seconds_per_build:.0f} seconds on your file.")
    if pack.budget.max_heartbeats:
        budget.append(f"`maxHeartbeats` is fixed at {pack.budget.max_heartbeats}. Do not set it: "
                      "an attempt that does is refused before Lean runs.")
    return _fill(_template(version, "context"), {
        "level": pack.level or "not stated",
        "field": pack.field or "not stated",
        "conventions": _bullets(pack.conventions),
        "assumptions": _bullets(assumptions),
        "not_in_library": _bullets(pack.not_in_mathlib),
        "forbidden": _bullets(pack.forbidden),
        "budget": _bullets(budget),
    })


def build_prompt(
    problem: Problem, step: Step, *, attempt: int, max_attempts: int, lean_version: str,
    libraries: str, previous: Previous | None = None, version: str = PROMPT_VERSION,
) -> Prompt:
    values = {
        "attempt": str(attempt),
        "max_attempts": str(max_attempts),
        "problem_title": problem.title,
        "problem_status": {"known": "a known result", "open": "an open problem",
                           "control": "a calibration control: the statement may be false"}[problem.status],
        "problem_informal": problem.informal,
        "step_part": f" {step.part}" if step.part else "",
        "step_informal": step.informal,
        "lean_version": lean_version,
        "libraries": libraries,
        "preamble": ("import ZtPreamble  -- which is:\n" + problem.preamble_source()).rstrip()
        if problem.preamble_source().strip() else "-- no imports: Lean's core library only",
        "theorem": step.theorem or "",
        "signature": step.signature or "",
        "context": _context_section(problem, step, version),
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
