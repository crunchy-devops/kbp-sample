"""Canaries: submissions the gate must reject, run through the gate on every run.

A gate that has never been seen to fail cannot be trusted. Each file under
`canaries/<problem>/<step>/` is the Lean text a dishonest or careless model might
send for that pinned claim. It names the forbidden move it makes and the rejection
it must get:

    -- move: axiom
    -- expect: forbidden_axiom

A canary passes only if the gate rejects it with exactly that outcome. Accepted is
an alarm. Rejected for another reason is a dead canary: a cheat that no longer
compiles tests Lean's parser, not the gate.

The test suite has fixtures of the same kind, against toy statements, to test the
gate's code. Canaries differ in two ways: they are written against the real pinned
claims, and they run before every real run, on the machine that will do it.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import gate
from .lean import REPO_ROOT
from .problem import Problem, ProblemError

CANARIES_DIR = REPO_ROOT / "canaries"

# One canary at least for each of these, somewhere in the folder, or the set is incomplete.
REQUIRED_MOVES = ("sorry", "axiom", "native_decide", "weakened_statement", "added_hypothesis")

# What a canary may expect. Never `accepted`, and never an outcome that means the
# platform itself misbehaved: a canary is there to see the gate say no, on purpose.
REJECTIONS = frozenset({
    gate.SORRY, gate.FORBIDDEN_AXIOM, gate.STATEMENT_MISMATCH, gate.NOT_A_THEOREM, gate.REDEFINITION,
    gate.THEOREM_MISSING, gate.REPLAY_FAILED, gate.BUDGET_OVERRIDE, gate.LEAN_ERROR,
})

_HEADER = re.compile(r"^--\s*(move|expect)\s*:\s*([A-Za-z0-9_+ -]+?)\s*$")
_MOVE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


@dataclass(frozen=True)
class Canary:
    problem: str
    step: str
    name: str
    move: str
    expect: str
    body: str

    @property
    def key(self) -> str:
        return f"{self.problem}/{self.step}/{self.name}"


@dataclass
class Result:
    canary: Canary
    outcome: str
    reason: str
    seconds: float

    @property
    def ok(self) -> bool:
        return self.outcome == self.canary.expect

    @property
    def state(self) -> str:
        if self.ok:
            return "rejected, as it must be"
        if self.outcome == gate.ACCEPTED:
            return "ACCEPTED: the gate has a hole"
        return f"DEAD: rejected as {self.outcome}, not as {self.canary.expect}"

    def to_json(self) -> dict:
        c = self.canary
        return {"canary": c.key, "move": c.move, "expect": c.expect, "outcome": self.outcome, "ok": self.ok,
                "reason": self.reason, "seconds": round(self.seconds, 2)}


def load_canaries(problems: list[Problem], root: Path = CANARIES_DIR) -> list[Canary]:
    """Every canary under `root`, in a fixed order. A malformed canary is an error:
    one that is silently skipped is one that silently stops guarding anything."""
    by_id = {p.id: p for p in problems}
    found: list[Canary] = []
    for path in sorted(root.glob("*/*/*.lean")) if root.is_dir() else []:
        where = str(path.relative_to(root.parent))
        pid, sid = path.parts[-3], path.parts[-2]
        problem = by_id.get(pid)
        if problem is None:
            raise ProblemError(f"{where}: no problem named {pid!r}")
        try:
            step = problem.step(sid)
        except KeyError:
            raise ProblemError(f"{where}: problem {pid} has no step {sid!r}") from None
        if not step.formalised:
            raise ProblemError(f"{where}: {pid}/{sid} has no Lean statement, so nothing can be submitted for it")
        text = path.read_text(encoding="utf-8")
        header: dict[str, str] = {}
        for line in text.splitlines():
            match = _HEADER.match(line)
            if not match:
                if line.strip() and not line.startswith("--"):
                    break
                continue
            header.setdefault(match.group(1), match.group(2).strip())
        move, expect = header.get("move", ""), header.get("expect", "")
        if not _MOVE.match(move):
            raise ProblemError(f"{where}: a canary names its move in a first comment, such as `-- move: axiom`")
        if expect not in REJECTIONS:
            raise ProblemError(f"{where}: `-- expect:` must be one of {', '.join(sorted(REJECTIONS))}")
        found.append(Canary(pid, sid, path.stem, move, expect, text))
    return found


def missing_moves(canaries: list[Canary]) -> list[str]:
    seen = {c.move for c in canaries}
    return [m for m in REQUIRED_MOVES if m not in seen]


def run_canaries(canaries: list[Canary], problems: list[Problem], config: gate.GateConfig | None = None) -> list[Result]:
    by_id = {p.id: p for p in problems}
    results: list[Result] = []
    for c in canaries:
        problem = by_id[c.problem]
        with tempfile.TemporaryDirectory(prefix="ztproof-") as tmp:
            verdict = gate.check_attempt(problem, problem.step(c.step), c.body, Path(tmp), config)
        results.append(Result(c, verdict.outcome, verdict.reason, sum(s.seconds for s in verdict.stages)))
    return results


def render(results: list[Result], missing: list[str]) -> str:
    alive = sum(1 for r in results if r.ok)
    out = [f"# Canaries: {alive} of {len(results)} rejected as they must be", "",
           "Each canary is a submission that makes one forbidden move against a pinned claim. The gate has to "
           "reject it, and for the stated reason.", "",
           "| Canary | Move | Must be rejected as | Gate said | Result |", "| --- | --- | --- | --- | --- |"]
    for r in results:
        out.append(f"| `{r.canary.key}` | {r.canary.move} | `{r.canary.expect}` | `{r.outcome}` | {r.state} |")
    if missing:
        out += ["", f"W> No canary makes the move: {', '.join(missing)}. The set is incomplete."]
    return "\n".join(out) + "\n"
