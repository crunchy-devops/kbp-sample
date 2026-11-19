"""Command line. Each subcommand is one stage, so that CI can run them as separate steps."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from . import __version__, calibration, canary, gate, lean, solutions
from .generator import DEFAULT_EFFORT, DEFAULT_MODEL
from .pipeline import (BATCHES_DIR, PROBLEMS_DIR, Run, check_statements, generate_round, init_run,
                       recheck_proofs, verify_round)
from .problem import Batch, ProblemError, load_all, load_batches, load_problem
from .report import render, render_document


def _config(args: argparse.Namespace) -> gate.GateConfig:
    return gate.GateConfig(compile_timeout=args.timeout, check_timeout=args.timeout)


def _run(args: argparse.Namespace) -> Run:
    return Run(Path(args.run))


def _write_report(run: Run, *, summary: bool = False) -> None:
    text = render(run)
    (run.path / "REPORT.md").write_text(text, encoding="utf-8")
    for problem in run.documents:
        target = run.path / problem.id
        target.mkdir(parents=True, exist_ok=True)
        (target / "REPORT.md").write_text(render_document(run, problem), encoding="utf-8")
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY") if summary else None
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def _batch(args: argparse.Namespace, problems: list) -> Batch | None:
    if not args.batch:
        return None
    for batch in load_batches(BATCHES_DIR, {p.id for p in problems}):
        if batch.id == args.batch:
            return batch
    raise ProblemError(f"no batch named {args.batch!r} in batches/")


def _outside_golden(problems: list) -> list:
    """The problems that are not part of a golden set. A run that names no batch
    means these: canaries and false statements are not results."""
    golden = {pid for b in load_batches(BATCHES_DIR, {p.id for p in problems}) if b.calibration is not None
              for pid in b.problems}
    return [p for p in problems if p.id not in golden]


def _start(args: argparse.Namespace) -> Run:
    problems = load_all(PROBLEMS_DIR)
    batch = _batch(args, problems)
    if batch is None and not args.only:
        problems = _outside_golden(problems)
    cleared = None
    if args.calibration:
        cleared = calibration.clearance(args.calibration, model=args.model, effort=args.effort,
                                        max_attempts=args.max_attempts)
    return init_run(
        problems=problems, only=args.only or None, max_attempts=args.max_attempts, model=args.model,
        effort=args.effort, run_id=args.run_id, force=args.force, batch=batch, calibrated_by=cleared,
    )


def _output(**values) -> None:
    """Hand values to the next CI step."""
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            for key, value in values.items():
                fh.write(f"{key}={value}\n")


def cmd_golden(args: argparse.Namespace) -> int:
    """The golden set against the gate. No model, no key, no run directory."""
    problems = {p.id: p for p in load_all(PROBLEMS_DIR)}
    batches = [b for b in load_batches(BATCHES_DIR, set(problems)) if b.calibration is not None]
    if not batches:
        print("ztproof: no batch has a [calibration] table, so there is no golden set", file=sys.stderr)
        return 1
    failures = 0
    for batch in batches:
        print(f"{batch.id}: {batch.title}")
        for r in calibration.check_references(batch, problems, _config(args)):
            print(f"  {'ok    ' if r.ok else 'FAILED'} {r.key:40} {r.outcome:16} {r.seconds:6.1f} s {r.max_rss_mb:7.0f} MB")
            if not r.ok:
                failures += 1
                print(f"         {r.detail}")
    return 1 if failures else 0


def cmd_canaries(args: argparse.Namespace) -> int:
    """Every canary through the gate. No model, no key. Fails unless each one is
    rejected, and for the reason it was written for."""
    problems = load_all(PROBLEMS_DIR)
    canaries = canary.load_canaries(problems)
    missing = canary.missing_moves(canaries)
    if args.only:
        canaries = [c for c in canaries if c.problem in args.only]
        missing = []            # a partial run says nothing about the whole set
    if not canaries:
        print("ztproof: no canaries found under canaries/", file=sys.stderr)
        return 1
    results = canary.run_canaries(canaries, problems, _config(args))
    text = canary.render(results, missing)
    print(text)
    if args.run:
        calibration._write_json(Path(args.run) / "canaries.json",
                                {"complete": not missing, "missing_moves": missing,
                                 "results": [r.to_json() for r in results]})
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY") if args.summary else None
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    if missing or not all(r.ok for r in results):
        print("ztproof: the canaries did not all die as they should. Do not trust the gate until they do.",
              file=sys.stderr)
        return 1
    return 0


def cmd_solutions(args: argparse.Namespace) -> int:
    """Every reference solution through the gate. No model, no key, nothing recorded."""
    problems = load_all(PROBLEMS_DIR)
    found = solutions.load_solutions(problems)
    if args.only:
        found = [s for s in found if s.problem in args.only]
    if not found:
        print("ztproof: no reference solutions found under solutions/", file=sys.stderr)
        return 1
    results = solutions.run_solutions(found, problems, _config(args))
    text = solutions.render(results)
    print(text)
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY") if args.summary else None
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    failed = [r for r in results if not r.ok]
    for r in failed:
        print(f"ztproof: {r.solution.key}: {r.outcome}: {r.reason}", file=sys.stderr)
    return 1 if failed else 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Stage 0: go or no-go, and the worker pools, from a finished golden run."""
    run = _run(args)
    next_imports: list[str] | None = None
    if args.next_batch is not None:
        everything = load_all(PROBLEMS_DIR)
        by_id = {p.id: p for p in everything}
        if args.next_batch == "":
            following = _outside_golden(everything)
        else:
            named = [b for b in load_batches(BATCHES_DIR, set(by_id)) if b.id == args.next_batch]
            if not named:
                raise ProblemError(f"no batch named {args.next_batch!r} in batches/")
            following = [by_id[pid] for pid in named[0].problems]
        next_imports = sorted({i for p in following for i in p.imports})
    result = calibration.calibrate(run, tpm_limit=args.tpm_limit, next_imports=next_imports)
    calibration.save(run, result)
    text = calibration.render(result)
    print(text)
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY") if args.summary else None
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    _output(go=str(result.go).lower(), verify_jobs=result.sizing["verify_jobs"],
            generate_jobs=result.sizing["generate_jobs"])
    if not result.go:
        print("ztproof: Stage 0 is a no-go. The batch must not start.", file=sys.stderr)
        return 1
    return 0


def cmd_batches(args: argparse.Namespace) -> int:
    problems = {p.id: p for p in load_all(PROBLEMS_DIR)}
    for batch in load_batches(BATCHES_DIR, set(problems)):
        print(f"{batch.id}: {batch.title}")
        for pid in batch.problems:
            p = problems[pid]
            ready = sum(1 for s in p.steps if s.formalised)
            print(f"  {pid:20} {p.document.cite():24} {ready} of {len(p.steps)} claims have a Lean statement")
    return 0


def cmd_problems(args: argparse.Namespace) -> int:
    for p in load_all(PROBLEMS_DIR):
        print(f"{p.id}: {p.title} [{p.status}; imports: {', '.join(p.imports) or 'core only'}]")
        if p.context:
            assumed = ", ".join(f"{a.name} ({'reviewed by ' + a.reviewed_by if a.reviewed_by else 'NOT REVIEWED'})"
                                for a in p.context.assumptions) or "nothing"
            print(f"  context pack {p.context.sha256[:12]}: assumes {assumed}; "
                  f"budget {p.context.budget.max_attempts or 'unset'} attempts")
        for s in p.steps:
            state = f"theorem {s.theorem}" if s.formalised else "no Lean statement yet"
            review = f"reviewed by {s.reviewed_by}" if s.reviewed_by else "NOT REVIEWED"
            print(f"  {s.id:24} {s.part:5} {state}" + (f" ({review})" if s.formalised else ""))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Every pinned statement must elaborate. No model, no run directory."""
    failures = 0
    for p in load_all(PROBLEMS_DIR):
        for s in p.steps:
            if not s.formalised:
                print(f"skipped {p.id}/{s.id}: no Lean statement yet")
                continue
            with tempfile.TemporaryDirectory(prefix="ztproof-") as tmp:
                stage = gate.compile_statement(p, s, Path(tmp), _config(args))
            print(f"{'ok     ' if stage.passed else 'INVALID'} {p.id}/{s.id} ({stage.seconds:.1f} s)")
            if not stage.passed:
                failures += 1
                print(stage.detail)
    return 1 if failures else 0


def cmd_init(args: argparse.Namespace) -> int:
    run = _start(args)
    print(run.path)
    _output(run=run.path, run_id=run.meta["run_id"])
    return 0


def cmd_statements(args: argparse.Namespace) -> int:
    run = _run(args)
    ok = check_statements(run, _config(args))
    for t in run.targets:
        s = run.statement(t) or {}
        print(f"{'ok     ' if s.get('valid') else 'INVALID'} {t.key}" + (f"\n{s.get('detail')}" if not s.get("valid") else ""))
    _write_report(run)
    return 0 if ok else 1


def cmd_generate(args: argparse.Namespace) -> int:
    from .generator import AnthropicGenerator

    run = _run(args)
    if not run.open_steps():
        print("nothing to generate")
        return 0
    made = generate_round(run, AnthropicGenerator(run.meta["model"], run.meta["effort"]), jobs=args.jobs)
    print(f"{made} repl{'y' if made == 1 else 'ies'} recorded")
    if run.meta.get("aborted"):
        print(run.meta["aborted"], file=sys.stderr)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    run = _run(args)
    checked = verify_round(run, _config(args), jobs=args.jobs)
    for t in run.targets:
        verdicts = run.verdicts(t)
        if verdicts:
            last = verdicts[-1]
            print(f"{t.key} attempt {last['attempt']}: {last['outcome']}")
    print(f"{checked} attempt(s) verified")
    _write_report(run)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    run = _run(args)
    _write_report(run, summary=args.summary)
    print(render(run))
    # An unproved step is a result. A run that could not do its job is a failure.
    invalid = [t.key for t in run.targets if (run.statement(t) or {}).get("valid") is False]
    if run.meta.get("aborted") or invalid:
        print(f"ztproof: run incomplete: {run.meta.get('aborted') or 'invalid statements: ' + ', '.join(invalid)}",
              file=sys.stderr)
        return 1
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .generator import AnthropicGenerator

    run = _start(args)
    print(f"run directory: {run.path}")
    check_statements(run, _config(args))
    generator = AnthropicGenerator(run.meta["model"], run.meta["effort"])
    while run.open_steps():
        generate_round(run, generator, jobs=args.jobs)
        verify_round(run, _config(args), jobs=args.jobs)
    _write_report(run)
    print(render(run))
    return 0


def cmd_recheck(args: argparse.Namespace) -> int:
    results = recheck_proofs(load_all(PROBLEMS_DIR), _config(args))
    if not results:
        print("no accepted proofs on file yet")
    for r in results:
        print(f"{r.outcome:22} {r.key}  {r.reason}")
    return 0 if all(r.outcome == gate.ACCEPTED for r in results) else 1


def cmd_prompt(args: argparse.Namespace) -> int:
    """The exact first prompt for one step. Asks no model and costs nothing."""
    from .prompt import build_prompt

    problem = load_problem(Path(args.problems) / args.problem)
    step = problem.step(args.step)
    prompt = build_prompt(problem, step, attempt=1, max_attempts=problem.attempts_allowed(args.max_attempts),
                          lean_version="see lean-toolchain", libraries=", ".join(problem.imports) or "core only")
    print(f"<!-- system prompt -->\n{prompt.system}\n\n<!-- user prompt -->\n{prompt.user}")
    print(f"<!-- prompt {prompt.version}, SHA-256 {prompt.sha256} -->")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    problem = load_problem(Path(args.problems) / args.problem)
    step = problem.step(args.step)
    with tempfile.TemporaryDirectory(prefix="ztproof-") as tmp:
        verdict = gate.check_attempt(problem, step, Path(args.file).read_text(encoding="utf-8"), Path(tmp), _config(args))
    print(f"{verdict.outcome}: {verdict.reason}")
    for line in verdict.diagnostics:
        print(f"  {line}")
    return 0 if verdict.accepted else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ztproof", description=__doc__)
    parser.add_argument("--version", action="version", version=f"ztproof {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func, help_: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_)
        p.set_defaults(func=func)
        p.add_argument("--timeout", type=float, default=600.0, help="seconds allowed per Lean invocation")
        p.add_argument("--jobs", type=int, default=int(os.environ.get("ZTPROOF_JOBS") or 1),
                       help="model requests, or Lean checks, to run at the same time")
        return p

    def selection(p: argparse.ArgumentParser) -> None:
        p.add_argument("--batch", help="run the documents listed in batches/<BATCH>.toml")
        p.add_argument("--only", action="append", metavar="PROBLEM[/STEP]", help="restrict the run; repeatable")
        p.add_argument("--max-attempts", type=int, default=3,
                       help="attempts per step; a context pack may set a smaller budget for its problem")
        p.add_argument("--model", default=os.environ.get("ZTPROOF_MODEL") or DEFAULT_MODEL)
        p.add_argument("--effort", default=os.environ.get("ZTPROOF_EFFORT") or DEFAULT_EFFORT)
        p.add_argument("--run-id")
        p.add_argument("--force", action="store_true", help="also attempt steps that already have an accepted proof")
        p.add_argument("--calibration", metavar="RUN_ID",
                       help="the Stage 0 run that cleared this one; refused unless it was a go for this configuration")

    add("problems", cmd_problems, "list the pinned problems and their steps")
    add("batches", cmd_batches, "list the batches and how far each document is formalised")
    add("check", cmd_check, "check that every pinned statement elaborates")
    selection(add("init", cmd_init, "create a run directory and print its path"))
    for name, func, help_ in (
        ("statements", cmd_statements, "check that every pinned statement of the run elaborates"),
        ("generate", cmd_generate, "ask the model once per open step (holds the API key, runs no Lean)"),
        ("verify", cmd_verify, "put unverified replies through the gate (runs Lean, needs no key)"),
        ("report", cmd_report, "write and print the referee report of a run"),
    ):
        stage = add(name, func, help_)
        stage.add_argument("--run", required=True, help="run directory")
        if name == "report":
            stage.add_argument("--summary", action="store_true", help="also append it to the CI job summary")
    add("golden", cmd_golden, "check the golden set's reference proofs against the gate, with no model")
    k = add("canaries", cmd_canaries, "run every canary through the gate; each must be rejected for its own reason")
    k.add_argument("--only", action="append", metavar="PROBLEM", help="restrict to one problem's canaries; repeatable")
    k.add_argument("--run", help="also record the results in this run directory, as canaries.json")
    k.add_argument("--summary", action="store_true", help="also append the table to the CI job summary")
    so = add("solutions", cmd_solutions, "check the reference solutions under solutions/ against the gate, with no model")
    so.add_argument("--only", action="append", metavar="PROBLEM", help="restrict to one problem's solutions; repeatable")
    so.add_argument("--summary", action="store_true", help="also append the table to the CI job summary")
    c = add("calibrate", cmd_calibrate, "Stage 0: go or no-go and worker pools, from a finished golden run")
    c.add_argument("--run", required=True, help="run directory of the golden batch")
    c.add_argument("--next-batch", metavar="BATCH", help="the batch this calibration is meant to clear; an empty value means every problem outside the golden sets")
    c.add_argument("--tpm-limit", type=int, default=int(os.environ.get("ZTPROOF_TPM_LIMIT") or 0) or None,
                   help="the account's tokens-per-minute limit for the model, if known")
    c.add_argument("--summary", action="store_true", help="also append the result to the CI job summary")
    selection(add("run", cmd_run, "all stages in one process, for local use"))
    add("recheck", cmd_recheck, "re-run the gate on every accepted proof in proofs/")
    pr = add("prompt", cmd_prompt, "print the first prompt for one step, without asking a model")
    pr.add_argument("--problems", default=str(PROBLEMS_DIR))
    pr.add_argument("--problem", required=True)
    pr.add_argument("--step", required=True)
    pr.add_argument("--max-attempts", type=int, default=3)
    g = add("gate", cmd_gate, "run the gate on one Lean file by hand")
    g.add_argument("--problems", default=str(PROBLEMS_DIR))
    g.add_argument("--problem", required=True)
    g.add_argument("--step", required=True)
    g.add_argument("--file", required=True)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ProblemError, lean.ToolchainError, FileExistsError) as exc:
        print(f"ztproof: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
