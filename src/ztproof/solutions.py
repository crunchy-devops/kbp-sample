"""Reference solutions: the reader's seat, checked by the same gate as a model.

The training ladder asks the reader to write a solution by hand before watching a
model try. A reference solution is such a file, kept for comparison:
`solutions/<problem>/<step>.lean`, the Lean text a reader would send, starting with
two comments that say who wrote it and who reviewed it:

    -- written_by: a name and a date
    -- reviewed_by:

A reference solution is never sent to a model, never filed under `proofs/` and never
counted as a result of a run. It only has to pass the gate, today, on the pinned
toolchain: `ztproof solutions` checks that, with no model and no key. A solution that
stops passing is reported as such, like a golden reference proof.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import gate
from .lean import REPO_ROOT
from .problem import Problem, ProblemError

SOLUTIONS_DIR = REPO_ROOT / "solutions"

_HEADER = re.compile(r"^--\s*(written_by|reviewed_by)\s*:(.*)$")


@dataclass(frozen=True)
class Solution:
    problem: str
    step: str
    written_by: str
    reviewed_by: str
    body: str

    @property
    def key(self) -> str:
        return f"{self.problem}/{self.step}"


@dataclass
class Result:
    solution: Solution
    outcome: str
    reason: str
    seconds: float
    axioms: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.outcome == gate.ACCEPTED

    def to_json(self) -> dict:
        s = self.solution
        return {"solution": s.key, "written_by": s.written_by, "reviewed_by": s.reviewed_by,
                "outcome": self.outcome, "ok": self.ok, "reason": self.reason,
                "axioms": list(self.axioms), "seconds": round(self.seconds, 2)}


def load_solutions(problems: list[Problem], root: Path = SOLUTIONS_DIR) -> list[Solution]:
    """Every reference solution under `root`, in a fixed order. A file that names no
    pinned claim, or does not say who wrote it, is an error rather than a skip."""
    by_id = {p.id: p for p in problems}
    found: list[Solution] = []
    for path in sorted(root.glob("*/*.lean")) if root.is_dir() else []:
        where = str(path.relative_to(root.parent))
        pid, sid = path.parts[-2], path.stem
        problem = by_id.get(pid)
        if problem is None:
            raise ProblemError(f"{where}: no problem named {pid!r}")
        try:
            step = problem.step(sid)
        except KeyError:
            raise ProblemError(f"{where}: problem {pid} has no step {sid!r}") from None
        if not step.formalised:
            raise ProblemError(f"{where}: {pid}/{sid} has no Lean statement, so nothing can be solved")
        if step.expect == "not_proved":
            raise ProblemError(f"{where}: {pid}/{sid} is a negative control, and a false statement has no solution")
        text = path.read_text(encoding="utf-8")
        header: dict[str, str] = {}
        for line in text.splitlines():
            match = _HEADER.match(line)
            if not match:
                if line.strip() and not line.startswith("--"):
                    break
                continue
            header.setdefault(match.group(1), match.group(2).strip())
        if not header.get("written_by"):
            raise ProblemError(f"{where}: a reference solution says who wrote it, as `-- written_by: name, date`")
        if "reviewed_by" not in header:
            raise ProblemError(f"{where}: a reference solution says who reviewed it, as `-- reviewed_by:` "
                               "(left empty until somebody has)")
        found.append(Solution(pid, sid, header["written_by"], header["reviewed_by"], text))
    return found


def run_solutions(solutions: list[Solution], problems: list[Problem],
                  config: gate.GateConfig | None = None) -> list[Result]:
    by_id = {p.id: p for p in problems}
    results: list[Result] = []
    for s in solutions:
        problem = by_id[s.problem]
        with tempfile.TemporaryDirectory(prefix="ztproof-") as tmp:
            verdict = gate.check_attempt(problem, problem.step(s.step), s.body, Path(tmp), config)
        axioms = tuple((verdict.facts or {}).get("axioms", ()) or ())
        results.append(Result(s, verdict.outcome, verdict.reason, sum(st.seconds for st in verdict.stages), axioms))
    return results


def render(results: list[Result]) -> str:
    passed = sum(1 for r in results if r.ok)
    out = [f"# Reference solutions: {passed} of {len(results)} pass the gate", "",
           "Each is a solution written in the reader's seat. It is checked by the same gate as a model's "
           "attempt, and is never sent to a model or counted as a model's result.", "",
           "| Solution | Gate said | Axioms used | Reviewed by |", "| --- | --- | --- | --- |"]
    for r in results:
        axioms = ", ".join(f"`{a}`" for a in r.axioms) or "none"
        out.append(f"| `{r.solution.key}` | `{r.outcome}` | {axioms} | {r.solution.reviewed_by or 'nobody yet'} |")
    return "\n".join(out) + "\n"
