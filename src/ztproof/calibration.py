"""Stage 0: the calibration gate.

Before a production batch, the golden batch is run through the pipeline with
exactly the configuration the production batch will use. This module reads that
run and answers three questions:

  1. Go or no-go? Measured against the thresholds in the golden batch file.
  2. How many workers? From the memory one Lean check really used and the token
     rate one model request really consumed.
  3. For which configuration is the answer valid? A go says nothing about another
     model, prompt version, toolchain or Mathlib revision.

Criteria that concern stages the platform does not have are reported as not
evaluated. They are never reported as passed.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import __version__, gate
from .pipeline import GOLDEN_DIR, RUNS_DIR, Run, Target, _read_json, _write_json, platform_tree_sha256
from .problem import Batch, Problem, ProblemError
from .prompt import PROMPT_VERSION

HEADROOM = 0.8          # share of the machine's memory we allow the Lean workers to fill
DEFAULT_GENERATE_JOBS = 8

NOT_EVALUATED = [
    ("Canary ingestion: claims extracted from a document as JSON, matching an expected dependency graph",
     "the platform does not extract claims; they are pinned by hand"),
    ("Signature and semantic check: a formaliser and an adversarial auditor on the canary set",
     "the platform has no formalisation or audit stage; Lean statements are written and reviewed by people"),
    ("Containerised runners and their memory limit",
     "Lean runs directly on the CI runner; the memory figure below is per Lean process, not per container"),
]


@dataclass
class Criterion:
    name: str
    passed: bool | None      # None: reported, not enforced
    detail: str


@dataclass
class Calibration:
    run_id: str
    batch: str
    go: bool
    criteria: list[Criterion]
    metrics: dict
    sizing: dict
    configuration: dict
    configuration_sha256: str
    not_evaluated: list[dict] = field(default_factory=list)
    previous: dict | None = None

    def to_json(self) -> dict:
        return asdict(self)


# The part of the configuration that is chosen, and so can be compared before a run starts.
REQUESTED = ("platform_version", "platform_tree_sha256", "model_requested", "effort", "prompt_version", "max_attempts")


def configuration(run: Run) -> dict:
    """Everything a go depends on. Change any of it and the go no longer applies.

    The commit is not in it: committing an audit trail moves the commit and changes
    nothing about the platform. `platform_tree_sha256` covers the code, the prompts
    and the pinned toolchain instead.
    """
    m = run.meta
    served = sorted({g.get("model_served") for g in _generations(run) if g.get("model_served")})
    return {
        "platform_version": m["platform_version"],
        "platform_tree_sha256": m.get("platform_tree_sha256"),
        "model_requested": m["model"],
        "models_served": served,
        "effort": m["effort"],
        "prompt_version": m["prompt_version"],
        "max_attempts": m["max_attempts"],
        "imports": sorted({i for t in run.targets for i in t.problem.imports}),
        "lean_version": m.get("lean_version"),
        "mathlib": m.get("mathlib"),
        "runner": {k: os.environ[k] for k in ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH") if k in os.environ},
    }


def _generations(run: Run) -> list[dict]:
    out = []
    for t in run.targets:
        for d in run.attempt_dirs(t):
            if (d / "generation.json").is_file():
                out.append(_read_json(d / "generation.json"))
    return out


def _proved_at(run: Run, t: Target) -> int | None:
    for v in run.verdicts(t):
        if v["outcome"] == gate.ACCEPTED:
            return v["attempt"]
    return None


def measure(run: Run) -> dict:
    """Plain counts and measurements from the run's files. No judgement here."""
    positives = [t for t in run.targets if t.step.expect == "proved"]
    controls = [t for t in run.targets if t.step.expect == "not_proved"]
    proved_at = {t.key: _proved_at(run, t) for t in run.targets}
    outcomes: dict[str, int] = {}
    stage_seconds: dict[str, float] = {}
    peak_rss, gate_seconds = 0.0, []
    for t in run.targets:
        for v in run.verdicts(t):
            outcomes[v["outcome"]] = outcomes.get(v["outcome"], 0) + 1
            total = 0.0
            for s in v.get("stages", []):
                peak_rss = max(peak_rss, s.get("max_rss_mb", 0.0))
                stage_seconds[s["name"]] = max(stage_seconds.get(s["name"], 0.0), s["seconds"])
                total += s["seconds"]
            if v.get("stages"):
                gate_seconds.append(total)
    generations = [g for g in _generations(run) if not g.get("error")]
    tokens = [g["usage"].get("input_tokens", 0) + g["usage"].get("output_tokens", 0) for g in generations if g.get("usage")]
    seconds = [g.get("seconds", 0.0) for g in generations]
    # One worker sends one request at a time, so its token rate is tokens over wall time.
    rate = (sum(tokens) / (sum(seconds) / 60)) if tokens and sum(seconds) > 0 else None
    n = len(positives)
    return {
        "claims": n,
        "proved": sum(1 for t in positives if proved_at[t.key] is not None),
        "proved_first_attempt": sum(1 for t in positives if proved_at[t.key] == 1),
        "pass_rate": (sum(1 for t in positives if proved_at[t.key] is not None) / n) if n else None,
        "pass_at_1": (sum(1 for t in positives if proved_at[t.key] == 1) / n) if n else None,
        "not_proved": sorted(t.key for t in positives if proved_at[t.key] is None),
        "controls": len(controls),
        "controls_accepted": sorted(t.key for t in controls if proved_at[t.key] is not None),
        "invalid_statements": sorted(t.key for t in run.targets if (run.statement(t) or {}).get("valid") is False),
        "attempts": sum(outcomes.values()),
        "outcomes": dict(sorted(outcomes.items())),
        "requests": len(generations),
        "tokens_total": sum(tokens),
        "tokens_per_request_mean": round(sum(tokens) / len(tokens)) if tokens else None,
        "request_seconds_mean": round(sum(seconds) / len(seconds), 1) if seconds else None,
        "tokens_per_minute_per_worker": round(rate) if rate else None,
        "peak_lean_mb": peak_rss or None,
        "gate_seconds_mean": round(sum(gate_seconds) / len(gate_seconds), 2) if gate_seconds else None,
        "gate_seconds_max": round(max(gate_seconds), 2) if gate_seconds else None,
        "stage_seconds_max": {k: round(v, 2) for k, v in sorted(stage_seconds.items())},
    }


def machine() -> dict:
    try:
        ram_mb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 * 1024)
    except (ValueError, OSError):
        ram_mb = 0.0
    return {"ram_mb": round(ram_mb), "cpus": os.cpu_count() or 1}


def size_workers(metrics: dict, host: dict, tpm_limit: int | None, unmeasured: list[str] | None = None) -> dict:
    """Worker pools from what was measured.

    The note this stage follows gives one formula,
        max workers = min(available RAM / peak Lean RAM, TPM limit / tokens per minute).
    Here the two terms size two separate pools, because model requests and Lean
    checks never run at the same time: a round asks, then a round verifies. The
    single figure of the formula is reported as well.
    """
    peak = metrics.get("peak_lean_mb")
    rate = metrics.get("tokens_per_minute_per_worker")
    by_ram = int(host["ram_mb"] * HEADROOM // peak) if peak and host["ram_mb"] else None
    verify = max(1, min(host["cpus"], by_ram)) if by_ram is not None else 1
    if unmeasured:
        # No measurement, no parallelism. A Lean process that imports a library the
        # golden set never loaded may be several times larger than anything measured.
        verify = 1
    by_tpm = int(tpm_limit // rate) if tpm_limit and rate else None
    generate = max(1, by_tpm) if by_tpm is not None else DEFAULT_GENERATE_JOBS
    return {
        "verify_jobs": verify,
        "generate_jobs": generate,
        "max_workers": min(verify, generate),
        "basis": {
            "ram_mb": host["ram_mb"], "cpus": host["cpus"], "headroom": HEADROOM, "peak_lean_mb": peak,
            "workers_by_ram": by_ram, "tpm_limit": tpm_limit, "tokens_per_minute_per_worker": rate,
            "workers_by_tpm": by_tpm,
            "unmeasured_imports": unmeasured or [],
            "note": None if by_tpm is not None else
                    f"no token-per-minute limit given or no token rate measured: {DEFAULT_GENERATE_JOBS} requests at a time by default",
        },
    }


def _previous(run: Run, config_sha: str) -> dict | None:
    """The latest earlier calibration on file, for the same batch, to set side by side."""
    found = None
    for path in sorted(RUNS_DIR.glob("*/calibration.json")):
        if path.parent == run.path:
            continue
        data = _read_json(path)
        if data.get("batch") == (run.meta.get("batch") or {}).get("id"):
            found = {"run_id": data["run_id"], "go": data["go"],
                     "same_configuration": data.get("configuration_sha256") == config_sha,
                     "pass_rate": data["metrics"].get("pass_rate"), "pass_at_1": data["metrics"].get("pass_at_1"),
                     "peak_lean_mb": data["metrics"].get("peak_lean_mb")}
    return found


def calibrate(run: Run, tpm_limit: int | None = None, next_imports: list[str] | None = None) -> Calibration:
    """Judge a finished golden run against the thresholds it started with.

    `next_imports` lists what the batch to follow imports. Whatever the golden run
    did not import itself was not measured.
    """
    thresholds = run.meta.get("calibration")
    if thresholds is None:
        raise ProblemError(f"run {run.meta['run_id']} is not a calibration run: its batch has no [calibration] table")
    metrics = measure(run)
    criteria: list[Criterion] = []

    def add(name: str, passed: bool | None, detail: str) -> None:
        criteria.append(Criterion(name, passed, detail))

    add("The run completed", not run.meta.get("aborted") and not run.open_steps() and
        all(run.pending(t) is None for t in run.targets),
        run.meta.get("aborted") or "every claim was either proved or used its attempts")
    add("Every golden statement elaborates", not metrics["invalid_statements"],
        ", ".join(metrics["invalid_statements"]) or "all of them")
    add("No false statement was accepted", not metrics["controls_accepted"] if metrics["controls"] else None,
        (", ".join(metrics["controls_accepted"]) or f"{metrics['controls']} negative control(s), none accepted")
        if metrics["controls"] else "the batch holds no negative control")

    def share(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.0%}"

    rate_min = thresholds.get("min_pass_rate")
    add("Pass rate", None if rate_min is None else (metrics["pass_rate"] is not None and metrics["pass_rate"] >= rate_min),
        f"{metrics['proved']} of {metrics['claims']} proved ({share(metrics['pass_rate'])})"
        + (f", threshold {rate_min:.0%}" if rate_min is not None else ", no threshold set"))
    p1_min = thresholds.get("min_pass_at_1")
    add("Pass@1", None if p1_min is None else (metrics["pass_at_1"] is not None and metrics["pass_at_1"] >= p1_min),
        f"{metrics['proved_first_attempt']} of {metrics['claims']} at the first attempt ({share(metrics['pass_at_1'])})"
        + (f", threshold {p1_min:.0%}" if p1_min is not None else ", no threshold set: no baseline has been measured"))
    peak_max = thresholds.get("max_peak_lean_mb")
    peak = metrics["peak_lean_mb"]
    add("Peak memory of one Lean process", None if peak_max is None else (peak is not None and peak <= peak_max),
        (f"{peak:.0f} MB" if peak else "not measured") + (f", limit {peak_max:.0f} MB" if peak_max is not None else ", no limit set"))

    config = configuration(run)
    config_sha = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    unmeasured = sorted(set(next_imports or []) - set(config["imports"]))
    return Calibration(
        run_id=run.meta["run_id"],
        batch=(run.meta.get("batch") or {}).get("id") or "",
        go=all(c.passed is not False for c in criteria),
        criteria=criteria,
        metrics=metrics,
        sizing=size_workers(metrics, machine(), tpm_limit, unmeasured),
        configuration=config,
        configuration_sha256=config_sha,
        not_evaluated=[{"criterion": c, "why": w} for c, w in NOT_EVALUATED] + [
            {"criterion": f"Memory and time of a Lean check that imports {name}",
             "why": f"no golden claim imports {name}, so Lean checks will run one at a time"} for name in unmeasured],
        previous=_previous(run, config_sha),
    )


def render(c: Calibration) -> str:
    m, s = c.metrics, c.sizing
    verdict = "GO" if c.go else "NO-GO"
    out = [
        f"# Stage 0 calibration: {verdict}", "",
        f"Golden batch `{c.batch}`, run `{c.run_id}`. " + (
            "The production batch may start, with this configuration and no other."
            if c.go else "The production batch must not start. Find the cause before running a single new document."), "",
        "## Criteria", "", "| Criterion | Result | Measured |", "| --- | --- | --- |",
    ]
    for item in c.criteria:
        result = {True: "passed", False: "**FAILED**", None: "reported, not enforced"}[item.passed]
        out.append(f"| {item.name} | {result} | {item.detail} |")
    out += ["", "## Not evaluated", "",
            "These criteria concern stages the platform does not have. They were not checked, which is not the same as passed.", ""]
    out += [f"* {n['criterion']}: {n['why']}." for n in c.not_evaluated]
    out += ["", "## Measurements", "", "| Measure | Value |", "| --- | --- |",
            f"| Attempts verified | {m['attempts']} |",
            f"| Outcomes | {', '.join(f'{k}: {v}' for k, v in m['outcomes'].items()) or 'none'} |",
            f"| Golden claims not proved | {', '.join(m['not_proved']) or 'none'} |",
            f"| Model requests answered | {m['requests']} |",
            f"| Tokens per request, mean | {m['tokens_per_request_mean'] or 'n/a'} |",
            f"| Seconds per request, mean | {m['request_seconds_mean'] or 'n/a'} |",
            f"| Tokens per minute, one worker | {m['tokens_per_minute_per_worker'] or 'n/a'} |",
            f"| Peak memory of one Lean process | {str(m['peak_lean_mb']) + ' MB' if m['peak_lean_mb'] else 'n/a'} |",
            f"| Gate time per attempt, mean and max | {m['gate_seconds_mean'] or 'n/a'} s, {m['gate_seconds_max'] or 'n/a'} s |",
            "", "## Worker pools for the production batch", "",
            f"* Lean checks at a time: **{s['verify_jobs']}** (machine: {s['basis']['ram_mb']} MB, {s['basis']['cpus']} CPUs; "
            f"{s['basis']['headroom']:.0%} of memory over the peak gives {s['basis']['workers_by_ram']}"
            + (f"; held at 1 because {', '.join(s['basis']['unmeasured_imports'])} was not measured"
               if s['basis']['unmeasured_imports'] else "") + ")",
            f"* Model requests at a time: **{s['generate_jobs']}**" + (
                f" (limit {s['basis']['tpm_limit']} tokens per minute over the measured rate)" if s['basis']['workers_by_tpm'] is not None
                else f" ({s['basis']['note']})"),
            f"* Single figure, the smaller of the two: {s['max_workers']}"]
    if c.previous:
        p = c.previous

        def pct(v):
            return "n/a" if v is None else f"{v:.0%}"
        out += ["", "## Against the previous calibration", "",
                f"Run `{p['run_id']}` ({'GO' if p['go'] else 'NO-GO'}, "
                f"{'same' if p['same_configuration'] else 'a different'} configuration): pass rate {pct(p['pass_rate'])} then, "
                f"{pct(m['pass_rate'])} now; Pass@1 {pct(p['pass_at_1'])} then, {pct(m['pass_at_1'])} now."]
    out += ["", "## Configuration this result is valid for", "", "```json",
            json.dumps(c.configuration, indent=2, ensure_ascii=False), "```", "",
            f"Fingerprint (SHA-256): `{c.configuration_sha256}`", ""]
    return "\n".join(out)


def save(run: Run, c: Calibration) -> None:
    _write_json(run.path / "calibration.json", c.to_json())
    (run.path / "CALIBRATION.md").write_text(render(c), encoding="utf-8")


# --- the reference proofs, with no model -------------------------------------------


@dataclass
class Reference:
    key: str
    ok: bool
    outcome: str
    detail: str
    seconds: float = 0.0
    max_rss_mb: float = 0.0


def check_references(batch: Batch, problems: dict[str, Problem], config: gate.GateConfig | None = None,
                     golden_dir: Path = GOLDEN_DIR) -> list[Reference]:
    """The golden set against the gate, with no model in sight.

    Every claim expected to be proved must have a reference proof under `golden/`
    that the gate accepts today. Every negative control must have a statement that
    elaborates, and no reference proof at all. If this fails, the canary is dead
    and nothing measured with it means anything.
    """
    config = config or gate.GateConfig()
    out: list[Reference] = []
    for pid in batch.problems:
        problem = problems[pid]
        for step in problem.steps:
            key = f"{pid}/{step.id}"
            reference = golden_dir / pid / f"{step.id}.lean"
            if not step.formalised:
                out.append(Reference(key, False, "no_statement", "a golden claim must have a Lean statement"))
                continue
            with tempfile.TemporaryDirectory(prefix="ztproof-") as tmp:
                if step.expect == "not_proved":
                    if reference.is_file():
                        out.append(Reference(key, False, "reference_for_a_control",
                                             f"{reference} exists, and a false statement has no proof"))
                        continue
                    stage = gate.compile_statement(problem, step, Path(tmp), config)
                    out.append(Reference(key, stage.passed, "control_ready" if stage.passed else gate.STATEMENT_INVALID,
                                         "negative control: the statement elaborates, and there is nothing to accept"
                                         if stage.passed else stage.detail, stage.seconds, stage.max_rss_mb))
                    continue
                if not reference.is_file():
                    out.append(Reference(key, False, "no_reference", f"{reference} is missing"))
                    continue
                verdict = gate.check_attempt(problem, step, reference.read_text(encoding="utf-8"), Path(tmp), config)
            out.append(Reference(key, verdict.accepted, verdict.outcome, verdict.reason,
                                 sum(s.seconds for s in verdict.stages),
                                 max((s.max_rss_mb for s in verdict.stages), default=0.0)))
    return out


# --- clearing the next run ------------------------------------------------------------


def clearance(calibration_run: str, *, model: str, effort: str, max_attempts: int,
              runs_dir: Path = RUNS_DIR) -> dict:
    """The record a production run keeps of the calibration that cleared it.

    Refuses a calibration that was a no-go, and one made with another model, effort,
    prompt version, attempt budget or platform tree. What can only be known once the
    run is under way (the model that really answered, the Lean and Mathlib builds)
    is in the calibration's own file, for the reader to compare.
    """
    path = runs_dir / calibration_run / "calibration.json"
    if not path.is_file():
        raise ProblemError(f"no calibration on file for run {calibration_run!r}")
    data = _read_json(path)
    if not data["go"]:
        raise ProblemError(f"calibration {calibration_run} was a no-go: the batch must not start")
    now = {"platform_version": __version__, "platform_tree_sha256": platform_tree_sha256(), "model_requested": model,
           "effort": effort, "prompt_version": PROMPT_VERSION, "max_attempts": max_attempts}
    differs = [k for k in REQUESTED if data["configuration"].get(k) != now[k]]
    if differs:
        raise ProblemError(f"calibration {calibration_run} was made with another configuration "
                           f"({', '.join(differs)} differ): calibrate again")
    return {"run_id": data["run_id"], "batch": data["batch"], "configuration_sha256": data["configuration_sha256"],
            "verify_jobs": data["sizing"]["verify_jobs"], "generate_jobs": data["sizing"]["generate_jobs"]}
