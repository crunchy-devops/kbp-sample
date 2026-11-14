"""The referee report: what a run established, on what evidence, and what it did not.

Written for a reader who trusts neither the model nor this platform. Every claim
points at a file in the run directory, and every check names the tool that made it.
"""

from __future__ import annotations

import json

from . import gate
from .pipeline import Run, Target

STAGE_TOOL = {
    "statement": "`lean` elaborates the pinned statement, written by the platform",
    "compile": "`lean` elaborates the attempt without errors",
    "facts": "`lean/Gate.lean` reads both compiled modules as data",
    "policy": "same statement as kernel terms, a theorem, standard axioms only",
    "replay": "`leanchecker` replays every declaration of the attempt in the kernel",
}


def _status(run: Run, t: Target) -> tuple[str, dict | None]:
    statement = run.statement(t)
    if statement is None:
        return "statement not checked", None
    if not statement["valid"]:
        return "statement invalid (platform-side failure)", None
    verdicts = run.verdicts(t)
    for v in verdicts:
        if v["outcome"] == gate.ACCEPTED:
            return f"**proved**, attempt {v['attempt']} of {run.meta['max_attempts']}", v
    if run.pending(t) is not None:
        return "reply awaiting verification", None
    if not verdicts:
        return "not attempted", None
    return f"not proved after {len(verdicts)} attempt(s)", None


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def render(run: Run) -> str:
    m = run.meta
    out: list[str] = [
        f"# Referee report for run {m['run_id']}",
        "",
        "This report is generated from the files in this directory. It states what was checked, by which tool, "
        "and what was not checked. Nothing in it comes from a model's description of its own work.",
        "",
        "## The run",
        "",
        "| Item | Value |",
        "| --- | --- |",
        f"| Started (UTC) | {m['started']} |",
        f"| Platform | ztproof {m['platform_version']}, commit `{m['git_sha'][:12]}` |",
        f"| Model asked | `{m['model']}`, effort `{m['effort']}` |",
        f"| Prompt templates | `prompts/{m['prompt_version']}/` |",
        f"| Lean | {m.get('lean_version') or 'not recorded'} |",
        f"| Mathlib | {m.get('mathlib') or 'not used'} |",
        f"| Attempts allowed per step | {m['max_attempts']} |",
    ]
    if m.get("ci"):
        out.append(f"| CI | {_cell(json.dumps(m['ci']))} |")
    if m.get("aborted"):
        out += ["", f"W> The run was cut short: {m['aborted']}"]

    out += ["", "## Summary", "", "| Step | Result |", "| --- | --- |"]
    for t in run.targets:
        out.append(f"| `{t.key}` {_cell(t.step.part)} | {_status(run, t)[0]} |")
    for key, why in m.get("skipped", {}).items():
        out.append(f"| `{key}` | skipped: {why} |")

    for t in run.targets:
        status, accepted = _status(run, t)
        statement = run.statement(t) or {}
        out += [
            "", f"## `{t.key}` {t.step.part}".rstrip(), "",
            f"Result: {status}.", "",
            "### The claim", "",
            t.step.informal, "",
            "Pinned Lean statement:", "",
            "```lean",
            f"theorem {t.step.theorem} {t.step.signature}",
            "```", "",
            f"* Formalised by: {t.step.formalised_by}",
            f"* Reviewed by: {t.step.reviewed_by or 'nobody yet'}",
            f"* Statement fingerprint (SHA-256): `{statement.get('statement_sha256', 'n/a')}`",
        ]
        if not t.step.reviewed_by:
            out += ["", "W> No human has reviewed this Lean statement. The kernel checks that a proof matches the "
                    "statement; it cannot check that the statement says what the mathematics means."]
        if statement and not statement.get("valid"):
            out += ["", "The statement itself did not elaborate, so no model was asked:", "", "```text",
                    statement.get("detail", ""), "```"]
        if accepted:
            facts = accepted.get("facts") or {}
            out += ["", "### Evidence for acceptance", "", "| Check | Tool and criterion | Result | Seconds |",
                    "| --- | --- | --- | --- |"]
            for s in accepted["stages"]:
                out.append(f"| {s['name']} | {STAGE_TOOL.get(s['name'], '')} | "
                           f"{'passed' if s['passed'] else 'FAILED'} | {s['seconds']:.2f} |")
            out += [
                "",
                f"* Axioms the proof depends on: {', '.join(f'`{a}`' for a in facts.get('axioms', [])) or 'none'}",
                f"* Proof text: `proofs/{t.problem.id}/{t.step.id}.lean`, SHA-256 of the checked file `{accepted['attempt_sha256']}`",
                f"* Checked with: {accepted['lean_version']}" + (f", Mathlib {accepted['mathlib']}" if accepted.get("mathlib") else ""),
                "* To check it again yourself: `ztproof recheck`",
            ]
        verdicts = run.verdicts(t)
        if verdicts:
            out += ["", "### Attempt history", "", "| Attempt | Outcome | Reason | Files |", "| --- | --- | --- | --- |"]
            for v in verdicts:
                out.append(f"| {v['attempt']} | `{v['outcome']}` | {_cell(v['reason'])[:300]} | "
                           f"`{t.problem.id}/{t.step.id}/attempt-{v['attempt']:02d}/` |")

    out += [
        "", "## What this report does not establish", "",
        "* That a Lean statement faithfully expresses the informal claim. That is a human judgement, recorded above as the reviewer.",
        "* That the hypotheses of a statement are true. A step proved under hypotheses is an implication, no more.",
        "* That Lean's kernel, the compiler that built it, or the machine it ran on are free of defects.",
        "* Anything about steps listed as not proved, not attempted or skipped. A failure to prove is not a disproof.",
        "",
    ]
    return "\n".join(out)
