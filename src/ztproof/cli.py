"""Command line. Each subcommand is one stage, so that CI can run them as separate steps."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from . import __version__, gate, lean
from .generator import DEFAULT_EFFORT, DEFAULT_MODEL
from .pipeline import (PROBLEMS_DIR, Run, check_statements, generate_round, init_run,
                       recheck_proofs, verify_round)
from .problem import ProblemError, load_all, load_problem
from .report import render


def _config(args: argparse.Namespace) -> gate.GateConfig:
    return gate.GateConfig(compile_timeout=args.timeout, check_timeout=args.timeout)


def _run(args: argparse.Namespace) -> Run:
    return Run(Path(args.run))


def _write_report(run: Run, *, summary: bool = False) -> None:
    text = render(run)
    (run.path / "REPORT.md").write_text(text, encoding="utf-8")
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY") if summary else None
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def cmd_problems(args: argparse.Namespace) -> int:
    for p in load_all(PROBLEMS_DIR):
        print(f"{p.id}: {p.title} [{p.status}; imports: {', '.join(p.imports) or 'core only'}]")
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
    run = init_run(
        problems=load_all(PROBLEMS_DIR), only=args.only or None, max_attempts=args.max_attempts,
        model=args.model, effort=args.effort, run_id=args.run_id, force=args.force,
    )
    print(run.path)
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
    made = generate_round(run, AnthropicGenerator(run.meta["model"], run.meta["effort"]))
    print(f"{made} repl{'y' if made == 1 else 'ies'} recorded")
    if run.meta.get("aborted"):
        print(run.meta["aborted"], file=sys.stderr)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    run = _run(args)
    checked = verify_round(run, _config(args))
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

    run = init_run(
        problems=load_all(PROBLEMS_DIR), only=args.only or None, max_attempts=args.max_attempts,
        model=args.model, effort=args.effort, run_id=args.run_id, force=args.force,
    )
    print(f"run directory: {run.path}")
    check_statements(run, _config(args))
    generator = AnthropicGenerator(run.meta["model"], run.meta["effort"])
    while run.open_steps():
        generate_round(run, generator)
        verify_round(run, _config(args))
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
        return p

    def selection(p: argparse.ArgumentParser) -> None:
        p.add_argument("--only", action="append", metavar="PROBLEM[/STEP]", help="restrict the run; repeatable")
        p.add_argument("--max-attempts", type=int, default=3)
        p.add_argument("--model", default=os.environ.get("ZTPROOF_MODEL") or DEFAULT_MODEL)
        p.add_argument("--effort", default=os.environ.get("ZTPROOF_EFFORT") or DEFAULT_EFFORT)
        p.add_argument("--run-id")
        p.add_argument("--force", action="store_true", help="also attempt steps that already have an accepted proof")

    add("problems", cmd_problems, "list the pinned problems and their steps")
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
    selection(add("run", cmd_run, "all stages in one process, for local use"))
    add("recheck", cmd_recheck, "re-run the gate on every accepted proof in proofs/")
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
