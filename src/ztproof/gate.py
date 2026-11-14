"""The gate: from an untrusted Lean file to a verdict with its evidence.

Stages, in order. The first one that fails names the failure class.

  1. statement   our own `ZtStatement.lean` elaborates (else: our bug, not the model's)
  2. compile     the attempt elaborates without errors
  3. facts       lean/Gate.lean compares the two compiled modules as data
  4. policy      same theorem, same type, a theorem, standard axioms only
  5. replay      `leanchecker` re-checks every declaration of the attempt in the kernel

An attempt is accepted only if all five pass. Nothing a model says about its own
proof is read at any point.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import lean
from .problem import Problem, Step

ALLOWED_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})
# The header the platform writes above every attempt. The module comment is not
# decoration: it is a command, so Lean refuses any `import` that follows it, and the
# attempt's imports are exactly ours.
ATTEMPT_HEADER = (
    "import ZtPreamble\n\n"
    "/-! Untrusted attempt: everything below this line was written by a model. -/\n\n"
)
HEADER_LINES = ATTEMPT_HEADER.count("\n")

# Failure classes. `accepted` is the only one that is not a failure.
ACCEPTED = "accepted"
STATEMENT_INVALID = "statement_invalid"      # platform or problem bug
INFRASTRUCTURE = "infrastructure_error"      # platform bug or broken toolchain
LEAN_ERROR = "lean_error"
LEAN_TIMEOUT = "lean_timeout"
THEOREM_MISSING = "theorem_missing"
STATEMENT_MISMATCH = "statement_mismatch"
NOT_A_THEOREM = "not_a_theorem"
REDEFINITION = "redefinition"
SORRY = "sorry"
FORBIDDEN_AXIOM = "forbidden_axiom"
REPLAY_FAILED = "kernel_replay_failed"


@dataclass
class StageResult:
    name: str
    passed: bool
    seconds: float = 0.0
    detail: str = ""


@dataclass
class Verdict:
    outcome: str
    reason: str
    stages: list[StageResult] = field(default_factory=list)
    facts: dict | None = None
    diagnostics: list[str] = field(default_factory=list)
    attempt_sha256: str = ""
    statement_sha256: str = ""
    lean_version: str = ""
    mathlib: str | None = None

    @property
    def accepted(self) -> bool:
        return self.outcome == ACCEPTED

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class GateConfig:
    compile_timeout: float = 600.0
    check_timeout: float = 600.0


def write_statement(problem: Problem, step: Step, workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "ZtPreamble.lean").write_text(problem.preamble_source(), encoding="utf-8")
    (workdir / "ZtStatement.lean").write_text(problem.statement_source(step), encoding="utf-8")


def compile_statement(problem: Problem, step: Step, workdir: Path, config: GateConfig) -> StageResult:
    """Stage 1. Compiles the preamble and the pinned statement."""
    write_statement(problem, step, workdir)
    seconds = 0.0
    for module in ("ZtPreamble", "ZtStatement"):
        result = lean.compile_module(
            workdir, module, packages=problem.needs_packages, timeout=config.compile_timeout
        )
        seconds += result.process.seconds
        if result.process.timed_out:
            return StageResult("statement", False, seconds, f"{module}: timed out")
        if not result.ok:
            detail = "\n".join(d.render() for d in result.errors) or result.process.stderr.strip()
            return StageResult("statement", False, seconds, f"{module}: {detail}")
    return StageResult("statement", True, seconds)


def apply_policy(facts: dict) -> tuple[str, str]:
    """Stage 4. The whole acceptance policy, in one place and in plain Python.

    It reads the facts reported by lean/Gate.lean and ignores that file's own
    `ok` field: two independent readings of the same facts must agree.
    """
    if not facts.get("statement_found"):
        return STATEMENT_INVALID, "the pinned statement is missing from its own module"
    if not facts.get("attempt_found"):
        return THEOREM_MISSING, f"the attempt does not declare `{facts.get('theorem')}`"
    if facts.get("redefined") or facts.get("missing"):
        names = ", ".join(facts.get("redefined", []) + facts.get("missing", []))
        return REDEFINITION, f"the attempt supplies its own version of: {names}"
    if not facts.get("type_match"):
        return STATEMENT_MISMATCH, "the attempt proves a different statement from the pinned one"
    if facts.get("kind") != "theorem":
        return NOT_A_THEOREM, f"`{facts.get('theorem')}` is declared as {facts.get('kind')}, not as a theorem"
    axioms = set(facts.get("axioms", []))
    if "sorryAx" in axioms:
        return SORRY, "the proof contains `sorry`"
    extra = sorted(axioms - ALLOWED_AXIOMS)
    if extra:
        hint = " (`native_decide` trusts the compiler, not the kernel)" if any(
            "native_decide" in a or a.startswith("Lean.ofReduceBool") or a == "Lean.trustCompiler"
            for a in extra) else ""
        return FORBIDDEN_AXIOM, "the proof depends on non-standard axioms: " + ", ".join(extra) + hint
    return ACCEPTED, "same statement, a theorem, standard axioms only"


def check_attempt(
    problem: Problem, step: Step, body: str, workdir: Path, config: GateConfig | None = None
) -> Verdict:
    """Run the whole gate on `body`, the Lean text proposed for this step."""
    config = config or GateConfig()
    packages = problem.needs_packages
    source = ATTEMPT_HEADER + body.rstrip("\n") + "\n"
    verdict = Verdict(
        outcome=INFRASTRUCTURE, reason="",
        attempt_sha256=hashlib.sha256(source.encode()).hexdigest(),
        statement_sha256=problem.statement_sha256(step),
        lean_version=lean.lean_version(),
        mathlib=lean.mathlib_revision() if packages else None,
    )

    def stop(outcome: str, reason: str) -> Verdict:
        verdict.outcome, verdict.reason = outcome, reason
        return verdict

    # 1. statement
    stage = compile_statement(problem, step, workdir, config)
    verdict.stages.append(stage)
    if not stage.passed:
        return stop(STATEMENT_INVALID, stage.detail)

    # 2. compile the attempt
    (workdir / "ZtAttempt.lean").write_text(source, encoding="utf-8")
    compiled = lean.compile_module(workdir, "ZtAttempt", packages=packages, timeout=config.compile_timeout)
    for d in compiled.diagnostics:
        d.line = max(d.line - HEADER_LINES, 0)  # line numbers as the author of the body sees them
    verdict.diagnostics = [d.render() for d in compiled.diagnostics]
    if compiled.process.timed_out:
        verdict.stages.append(StageResult("compile", False, compiled.process.seconds, "timed out"))
        return stop(LEAN_TIMEOUT, f"Lean did not finish within {config.compile_timeout:.0f} s")
    if not compiled.ok:
        errors = compiled.errors
        detail = errors[0].render() if errors else compiled.process.stderr.strip()[:500]
        verdict.stages.append(StageResult("compile", False, compiled.process.seconds, detail))
        return stop(LEAN_ERROR, f"{len(errors)} Lean error(s); first: {detail}")
    verdict.stages.append(StageResult("compile", True, compiled.process.seconds))

    # 3. facts
    gate = lean.run_gate(workdir, step.theorem or "", packages=packages, timeout=config.check_timeout)
    try:
        facts = json.loads(gate.stdout.strip().splitlines()[-1])
        if not isinstance(facts, dict):
            raise ValueError("not an object")
    except (IndexError, ValueError):
        detail = (gate.stderr.strip() or gate.stdout.strip() or "no output")[:1000]
        verdict.stages.append(StageResult("facts", False, gate.seconds, detail))
        return stop(INFRASTRUCTURE, f"the gate program produced no facts: {detail}")
    verdict.facts = facts
    verdict.stages.append(StageResult("facts", True, gate.seconds))

    # 4. policy
    outcome, reason = apply_policy(facts)
    agrees = bool(facts.get("ok")) == (outcome == ACCEPTED)
    verdict.stages.append(StageResult("policy", outcome == ACCEPTED and agrees, 0.0, reason))
    if not agrees:
        return stop(INFRASTRUCTURE, f"the two readings of the facts disagree (Python: {outcome})")
    if outcome != ACCEPTED:
        return stop(outcome, reason)

    # 5. kernel replay
    replay = lean.run_replay(workdir, packages=packages, timeout=config.check_timeout)
    if replay.returncode != 0:
        detail = "timed out" if replay.timed_out else (replay.stderr.strip() or replay.stdout.strip())[:1000]
        verdict.stages.append(StageResult("replay", False, replay.seconds, detail))
        return stop(REPLAY_FAILED, f"the kernel rejected the compiled attempt on replay: {detail}")
    verdict.stages.append(StageResult("replay", True, replay.seconds))

    return stop(ACCEPTED, reason)
