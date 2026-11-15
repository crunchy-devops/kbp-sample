"""The Python glue: conjecture -> prompt -> Lean 4 -> verdict, with every step on disk.

A run is a directory under `audit/runs/`. It is both the working state and the
audit trail: each stage reads what the previous one wrote, so the stages can run as
separate CI steps. That separation matters. `generate` holds the API key and never
runs Lean; `verify` runs untrusted Lean and never sees the key.

    <run>/run.json
    <run>/attempts.jsonl                       one line per verified attempt
    <run>/<problem>/<step>/statement.json      does our own statement elaborate?
    <run>/<problem>/<step>/attempt-NN/
        prompt.md        exactly what was sent
        generation.json  model, tokens, timing, stop reason
        reply.md         exactly what came back
        attempt.lean     the Lean text extracted from the reply
        verdict.json     the gate's stages, facts and decision
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import __version__, gate, lean
from .generator import Generation, Generator
from .problem import Batch, Problem, Step, load_all
from .prompt import PROMPT_VERSION, Previous, ProtocolViolation, build_prompt, extract_lean

REPO_ROOT = lean.REPO_ROOT
PROBLEMS_DIR = REPO_ROOT / "problems"
BATCHES_DIR = REPO_ROOT / "batches"
PROOFS_DIR = REPO_ROOT / "proofs"
GOLDEN_DIR = REPO_ROOT / "golden"
RUNS_DIR = REPO_ROOT / "audit" / "runs"

# Outcomes decided before the gate is reached.
GENERATION_FAILED = "generation_failed"
REFUSAL = "refusal"
TRUNCATED = "truncated"
PROTOCOL_VIOLATION = "protocol_violation"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_sha() -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    return out.stdout.strip() or os.environ.get("GITHUB_SHA") or "unknown"


# What decides how the platform behaves. The audit trail and the proofs are left out
# on purpose: committing a run must not change the identity of the platform.
PLATFORM_FILES = ("src", "lean", "prompts", "lean-toolchain", "lakefile.toml", "lake-manifest.json", "pyproject.toml")


def platform_tree_sha256() -> str:
    """One fingerprint for the platform's own code, prompts and pinned toolchain, from Git's index."""
    out = subprocess.run(["git", "ls-files", "-s", "--", *PLATFORM_FILES], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=False)
    if out.returncode != 0 or not out.stdout.strip():
        return "unknown"
    return hashlib.sha256(out.stdout.encode()).hexdigest()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Target:
    problem: Problem
    step: Step

    @property
    def key(self) -> str:
        return f"{self.problem.id}/{self.step.id}"


class Run:
    def __init__(self, path: Path, problems: list[Problem] | None = None) -> None:
        self.path = path
        self.meta = _read_json(path / "run.json")
        by_id = {p.id: p for p in (problems if problems is not None else load_all(PROBLEMS_DIR))}
        self.documents = [by_id[pid] for pid in self.meta.get("documents", []) if pid in by_id]
        self.targets = [
            Target(by_id[pid], by_id[pid].step(sid))
            for pid, sid in (key.split("/") for key in self.meta["steps"])
        ]

    # --- layout ------------------------------------------------------------------

    def step_dir(self, t: Target) -> Path:
        return self.path / t.problem.id / t.step.id

    def attempt_dirs(self, t: Target) -> list[Path]:
        return sorted(self.step_dir(t).glob("attempt-[0-9][0-9]"))

    def save_meta(self) -> None:
        _write_json(self.path / "run.json", self.meta)

    # --- state, always derived from the files ------------------------------------

    def statement(self, t: Target) -> dict | None:
        path = self.step_dir(t) / "statement.json"
        return _read_json(path) if path.is_file() else None

    def verdicts(self, t: Target) -> list[dict]:
        return [_read_json(d / "verdict.json") for d in self.attempt_dirs(t) if (d / "verdict.json").is_file()]

    def pending(self, t: Target) -> Path | None:
        """An attempt that has a reply but no verdict yet."""
        for d in self.attempt_dirs(t):
            if (d / "generation.json").is_file() and not (d / "verdict.json").is_file():
                return d
        return None

    def wants_generation(self, t: Target) -> bool:
        statement = self.statement(t)
        if self.meta.get("aborted") or not statement or not statement.get("valid"):
            return False
        if self.pending(t) is not None:
            return False
        verdicts = self.verdicts(t)
        if any(v["outcome"] == gate.ACCEPTED for v in verdicts):
            return False
        return len(self.attempt_dirs(t)) < self.meta["max_attempts"]

    def open_steps(self) -> list[Target]:
        return [t for t in self.targets if self.wants_generation(t)]


def existing_proof(t: Target) -> dict | None:
    """The record of an accepted proof already in `proofs/`, if it is for the
    statement as it is pinned today."""
    record = PROOFS_DIR / t.problem.id / f"{t.step.id}.json"
    if not record.is_file():
        return None
    data = _read_json(record)
    return data if data.get("statement_sha256") == t.problem.statement_sha256(t.step) else None


def init_run(
    *, problems: list[Problem], only: list[str] | None, max_attempts: int, model: str,
    effort: str, run_id: str | None = None, force: bool = False, runs_dir: Path = RUNS_DIR,
    batch: Batch | None = None, calibrated_by: dict | None = None,
) -> Run:
    """Create a run directory listing the steps this run will work on.

    A batch restricts the run to its documents; `only` restricts it further. A
    golden batch, one with calibration thresholds, is always attempted in full:
    a calibration that skipped what was proved last time would measure nothing.
    """
    calibration = batch.calibration if batch is not None else None
    force = force or calibration is not None
    if batch is not None:
        by_id = {p.id: p for p in problems}
        problems = [by_id[pid] for pid in batch.problems]
    selected: list[Target] = []
    skipped: dict[str, str] = {}
    for p in problems:
        for s in p.steps:
            t = Target(p, s)
            if only and p.id not in only and t.key not in only:
                continue
            if not s.formalised:
                skipped[t.key] = "no Lean statement yet"
            elif not force and existing_proof(t) is not None:
                skipped[t.key] = "already proved, see proofs/"
            else:
                selected.append(t)
    sha = _git_sha()
    run_id = run_id or f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{sha[:7]}"
    path = runs_dir / run_id
    if path.exists():
        raise FileExistsError(f"run {run_id} already exists")
    _write_json(path / "run.json", {
        "schema": 1,
        "run_id": run_id,
        "started": _now(),
        "platform_version": __version__,
        "git_sha": sha,
        "platform_tree_sha256": platform_tree_sha256(),
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "effort": effort,
        "max_attempts": max_attempts,
        "lean_version": None,
        "mathlib": None,
        "ci": {k: os.environ[k] for k in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_WORKFLOW",
                                          "GITHUB_REF_NAME", "GITHUB_ACTOR") if k in os.environ},
        "batch": {"id": batch.id, "title": batch.title} if batch else None,
        # Thresholds, copied here when the run is a Stage 0 calibration, so that the
        # run is judged by the thresholds it started with.
        "calibration": calibration,
        # The Stage 0 run that cleared this one, if any.
        "calibrated_by": calibrated_by,
        "documents": [p.id for p in problems if not only or p.id in only
                      or any(k.startswith(p.id + "/") for k in only)],
        "steps": [t.key for t in selected],
        "skipped": skipped,
    })
    return Run(path, problems)


def check_statements(run: Run, config: gate.GateConfig | None = None) -> bool:
    """Stage 1 for every step of the run. A statement that does not elaborate is our
    bug or the problem author's, and no model is asked to prove it."""
    config = config or gate.GateConfig()
    all_valid = True
    run.meta["lean_version"] = lean.lean_version()
    if any(t.problem.needs_packages for t in run.targets):
        run.meta["mathlib"] = lean.mathlib_revision()
    run.save_meta()
    for t in run.targets:
        with tempfile.TemporaryDirectory(prefix="ztproof-") as tmp:
            stage = gate.compile_statement(t.problem, t.step, Path(tmp), config)
        _write_json(run.step_dir(t) / "statement.json", {
            "valid": stage.passed,
            "detail": stage.detail,
            "seconds": round(stage.seconds, 2),
            "statement_sha256": t.problem.statement_sha256(t.step),
            "theorem": t.step.theorem,
            "signature": t.step.signature,
            "formalised_by": t.step.formalised_by,
            "reviewed_by": t.step.reviewed_by,
            "document": t.problem.document.cite(),
            "locator": t.step.locator,
        })
        all_valid = all_valid and stage.passed
    return all_valid


def _previous(run: Run, t: Target) -> Previous | None:
    dirs = [d for d in run.attempt_dirs(t) if (d / "verdict.json").is_file()]
    if not dirs:
        return None
    last = dirs[-1]
    verdict = _read_json(last / "verdict.json")
    body = (last / "attempt.lean").read_text(encoding="utf-8") if (last / "attempt.lean").is_file() else ""
    return Previous(
        attempt=int(last.name.split("-")[1]), body=body, outcome=verdict["outcome"],
        reason=verdict["reason"], diagnostics=verdict.get("diagnostics", []),
    )


def _libraries(problem: Problem, run: Run) -> str:
    if not problem.imports:
        return "none beyond Lean's core library (`Init`); Mathlib is not available"
    text = ", ".join(problem.imports)
    if problem.needs_packages and run.meta.get("mathlib"):
        text += f"; Mathlib is pinned at {run.meta['mathlib']}"
    return text


def _infrastructure_failure(generation: Generation) -> bool:
    return bool(generation.error) and generation.stop_reason not in ("refusal", "max_tokens")


def generate_round(run: Run, generator: Generator, jobs: int = 1) -> int:
    """Ask the model once for every step that still wants an attempt. Runs no Lean.

    With `jobs` above one the requests go out in parallel, which is what makes a
    batch of documents practical. The first request always goes alone: if the key
    or the network is broken we want one record of it, not one per step.
    """
    prepared: list[tuple[Target, Path, object]] = []
    for t in run.open_steps():
        number = len(run.attempt_dirs(t)) + 1
        directory = run.step_dir(t) / f"attempt-{number:02d}"
        prompt = build_prompt(
            t.problem, t.step, attempt=number, max_attempts=run.meta["max_attempts"],
            lean_version=run.meta.get("lean_version") or "see lean-toolchain",
            libraries=_libraries(t.problem, run), previous=_previous(run, t),
        )
        directory.mkdir(parents=True)
        (directory / "prompt.md").write_text(
            f"<!-- system prompt -->\n{prompt.system}\n\n<!-- user prompt -->\n{prompt.user}", encoding="utf-8")
        prepared.append((t, directory, prompt))

    def record(t: Target, directory: Path, prompt, generation: Generation) -> None:
        (directory / "reply.md").write_text(generation.text, encoding="utf-8")
        data = generation.to_json()
        del data["text"]
        data.update(prompt_version=prompt.version, prompt_sha256=prompt.sha256, generated=_now())
        _write_json(directory / "generation.json", data)
        if _infrastructure_failure(generation) and not run.meta.get("aborted"):
            # Authentication, quota, network: asking again would only fill the
            # trail with the same infrastructure failure.
            run.meta["aborted"] = f"generation stopped at {t.key}: {generation.error}"
            run.save_meta()

    made = 0
    if prepared:
        t, directory, prompt = prepared[0]
        record(t, directory, prompt, generator.generate(prompt))
        made = 1
    rest = prepared[1:] if not run.meta.get("aborted") else []
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        generations = list(pool.map(lambda item: generator.generate(item[2]), rest))
    for (t, directory, prompt), generation in zip(rest, generations):
        record(t, directory, prompt, generation)
        made += 1
    # A prompt prepared but never sent would otherwise count as an attempt.
    for _, directory, _ in prepared[made:]:
        (directory / "prompt.md").unlink()
        directory.rmdir()
    return made


def _pre_gate_verdict(generation: dict, reply: str) -> tuple[gate.Verdict | None, str]:
    """Outcomes that never reach Lean. Returns (verdict, extracted body)."""
    if generation.get("error"):
        outcome = {"refusal": REFUSAL, "max_tokens": TRUNCATED}.get(generation.get("stop_reason", ""), GENERATION_FAILED)
        return gate.Verdict(outcome=outcome, reason=generation["error"]), ""
    try:
        return None, extract_lean(reply)
    except ProtocolViolation as exc:
        return gate.Verdict(outcome=PROTOCOL_VIOLATION, reason=str(exc)), ""


def verify_round(run: Run, config: gate.GateConfig | None = None, proofs_dir: Path = PROOFS_DIR,
                 jobs: int = 1) -> int:
    """Put every unverified reply through the gate and record the verdict.

    The gate runs for several attempts at once when `jobs` allows; the records are
    always written in the run's own step order, so the trail does not depend on
    which Lean process happened to finish first.
    """
    pending = [(t, d) for t in run.targets if (d := run.pending(t)) is not None]

    def judge(item: tuple[Target, Path]) -> tuple[dict, gate.Verdict, str]:
        t, directory = item
        generation = _read_json(directory / "generation.json")
        reply = (directory / "reply.md").read_text(encoding="utf-8")
        verdict, body = _pre_gate_verdict(generation, reply)
        if verdict is None:
            (directory / "attempt.lean").write_text(body, encoding="utf-8")
            with tempfile.TemporaryDirectory(prefix="ztproof-") as tmp:
                verdict = gate.check_attempt(t.problem, t.step, body, Path(tmp), config)
        return generation, verdict, body

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        judged = list(pool.map(judge, pending))

    checked = 0
    for (t, directory), (generation, verdict, body) in zip(pending, judged):
        number = int(directory.name.split("-")[1])
        data = verdict.to_json()
        data.update(problem=t.problem.id, step=t.step.id, attempt=number, verified=_now())
        _write_json(directory / "verdict.json", data)
        with (run.path / "attempts.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps({
                "schema": 1,
                "run_id": run.meta["run_id"],
                "platform_version": run.meta["platform_version"],
                "git_sha": run.meta["git_sha"],
                "verified": data["verified"],
                "batch": (run.meta.get("batch") or {}).get("id"),
                "problem": t.problem.id,
                "document": t.problem.document.cite(),
                "step": t.step.id,
                "locator": t.step.locator,
                "attempt": number,
                "max_attempts": run.meta["max_attempts"],
                "prompt_version": generation.get("prompt_version"),
                "prompt_sha256": generation.get("prompt_sha256"),
                "model_requested": generation.get("model_requested"),
                "model_served": generation.get("model_served"),
                "effort": generation.get("effort"),
                "fallback_ran": generation.get("fallback_ran"),
                "stop_reason": generation.get("stop_reason"),
                "usage": generation.get("usage"),
                "generation_seconds": round(generation.get("seconds", 0.0), 1),
                "outcome": verdict.outcome,
                "reason": verdict.reason,
                "stages": [{"name": s.name, "passed": s.passed, "seconds": round(s.seconds, 2),
                            "max_rss_mb": s.max_rss_mb} for s in verdict.stages],
                "axioms": (verdict.facts or {}).get("axioms"),
                "attempt_sha256": verdict.attempt_sha256,
                "statement_sha256": verdict.statement_sha256,
                "lean_version": verdict.lean_version,
                "mathlib": verdict.mathlib,
                "path": str(directory.relative_to(run.path)),
            }, ensure_ascii=False) + "\n")
        # A calibration run files nothing: the golden set has its reference proofs
        # under golden/, and proofs/ is for results, not for canaries.
        if verdict.accepted and run.meta.get("calibration") is None:
            target = proofs_dir / t.problem.id
            target.mkdir(parents=True, exist_ok=True)
            (target / f"{t.step.id}.lean").write_text(body, encoding="utf-8")
            _write_json(target / f"{t.step.id}.json", {
                "problem": t.problem.id,
                "step": t.step.id,
                "theorem": t.step.theorem,
                "statement_sha256": verdict.statement_sha256,
                "attempt_sha256": verdict.attempt_sha256,
                "axioms": (verdict.facts or {}).get("axioms", []),
                "run_id": run.meta["run_id"],
                "attempt": number,
                "model_served": generation.get("model_served"),
                "prompt_version": generation.get("prompt_version"),
                "lean_version": verdict.lean_version,
                "mathlib": verdict.mathlib,
                "accepted": data["verified"],
            })
        checked += 1
    return checked


@dataclass
class Recheck:
    key: str
    outcome: str
    reason: str


def recheck_proofs(problems: list[Problem], config: gate.GateConfig | None = None,
                   proofs_dir: Path = PROOFS_DIR) -> list[Recheck]:
    """Re-run the whole gate on every proof kept in `proofs/`, against the statements
    as pinned today. An accepted proof stays accepted only for as long as this passes."""
    by_id = {p.id: p for p in problems}
    results: list[Recheck] = []
    for record_path in sorted(proofs_dir.glob("*/*.json")):
        record = _read_json(record_path)
        key = f"{record['problem']}/{record['step']}"
        problem = by_id.get(record["problem"])
        try:
            step = problem.step(record["step"]) if problem else None
        except KeyError:
            step = None
        if problem is None or step is None or not step.formalised:
            results.append(Recheck(key, "orphan", "no pinned statement for this proof any more"))
            continue
        if problem.statement_sha256(step) != record["statement_sha256"]:
            results.append(Recheck(key, "stale", "the pinned statement changed after this proof was accepted"))
            continue
        body = record_path.with_suffix(".lean").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="ztproof-") as tmp:
            verdict = gate.check_attempt(problem, step, body, Path(tmp), config)
        results.append(Recheck(key, verdict.outcome, verdict.reason))
    return results
