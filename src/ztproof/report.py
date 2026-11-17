"""The referee report: what a run established, on what evidence, and what it did not.

Written for a reader who trusts neither the model nor this platform. Every claim
points at a file in the run directory, and every check names the tool that made it.
"""

from __future__ import annotations

import json

from . import gate
from .pipeline import Run, Target
from .problem import Problem

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
    control = t.step.expect == "not_proved"
    for v in verdicts:
        if v["outcome"] == gate.ACCEPTED:
            assumed = t.problem.assumptions_of(t.step)
            if assumed and not control:
                names = ", ".join(f"`{a.name}`" for a in assumed)
                return (f"**proved, assuming {names}**, attempt {v['attempt']} of {run.attempts_allowed(t)}"), v
            if control:
                return (f"**ACCEPTED, AND IT MUST NOT BE**: this statement is a negative control, known to be "
                        f"false (attempt {v['attempt']}). The gate has a hole"), v
            return f"**proved**, attempt {v['attempt']} of {run.attempts_allowed(t)}", v
    if run.pending(t) is not None:
        return "reply awaiting verification", None
    if not verdicts:
        return "not attempted", None
    if control:
        return (f"not proved after {len(verdicts)} attempt(s), which is the expected result: "
                "this statement is a negative control, known to be false"), None
    return f"not proved after {len(verdicts)} attempt(s)", None


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


CAVEATS = [
    "## What this report does not establish", "",
    "* That a Lean statement faithfully expresses the informal claim. That is a human judgement, recorded above as the reviewer.",
    "* That the hypotheses of a statement are true. A step proved under hypotheses is an implication, no more. "
    "This holds with full force for a named assumption that stands for theorems not formalised here.",
    "* That the claims listed are all the claims of the document. A claim nobody pinned was not examined.",
    "* That Lean's kernel, the compiler that built it, or the machine it ran on are free of defects.",
    "* Anything about steps listed as not proved, not attempted or skipped. A failure to prove is not a disproof.",
    "",
]


def _run_table(run: Run) -> list[str]:
    m = run.meta
    out = [
        "| Item | Value |",
        "| --- | --- |",
        f"| Run | `{m['run_id']}` |",
        f"| Started (UTC) | {m['started']} |",
        f"| Platform | ztproof {m['platform_version']}, commit `{m['git_sha'][:12]}` |",
        f"| Model asked | `{m['model']}`, effort `{m['effort']}` |",
        f"| Prompt templates | `prompts/{m['prompt_version']}/` |",
        f"| Lean | {m.get('lean_version') or 'not recorded'} |",
        f"| Mathlib | {m.get('mathlib') or 'not used'} |",
        f"| Attempts allowed per step | {m['max_attempts']}, or the context pack's budget where that is smaller |",
    ]
    if m.get("batch"):
        out.insert(3, f"| Batch | `{m['batch']['id']}`: {_cell(m['batch']['title'])} |")
    if m.get("calibration") is not None:
        out.append("| Kind of run | Stage 0 calibration on a golden set; see `CALIBRATION.md` in this directory |")
    elif m.get("calibrated_by"):
        c = m["calibrated_by"]
        out.append(f"| Stage 0 calibration | GO, run `{c['run_id']}`, configuration `{c['configuration_sha256'][:12]}` |")
    else:
        out.append("| Stage 0 calibration | none: this run was not cleared by a calibration |")
    if m.get("ci"):
        out.append(f"| CI | {_cell(json.dumps(m['ci']))} |")
    if m.get("aborted"):
        out += ["", f"W> The run was cut short: {m['aborted']}"]
    return out


def _tally(run: Run, problem: Problem) -> dict[str, int]:
    """Counts for one document. Every claim pinned for it falls in exactly one column."""
    counts = {"claims": len(problem.steps), "proved": 0, "not_proved": 0, "not_formalised": 0,
              "other": 0, "unreviewed": 0}
    targets = {t.step.id: t for t in run.targets if t.problem.id == problem.id}
    for step in problem.steps:
        if step.formalised and not step.reviewed_by:
            counts["unreviewed"] += 1
        t = targets.get(step.id)
        if not step.formalised:
            counts["not_formalised"] += 1
        elif t is None:
            counts["other"] += 1        # skipped: proved in an earlier run, or outside this run's selection
        elif _status(run, t)[1] is not None:
            counts["proved"] += 1
        elif run.verdicts(t):
            counts["not_proved"] += 1
        else:
            counts["other"] += 1
    return counts


def _context_lines(run: Run, problem: Problem) -> list[str]:
    """What the model was told beyond the statement, for the reader to check for themselves."""
    pack = problem.context
    if pack is None:
        return ["* Context pack: none. The model was given the statement and nothing else."]
    recorded = (run.meta.get("context_packs") or {}).get(problem.id)
    out = [f"* Context pack: `problems/{problem.id}/context.yaml`, SHA-256 `{recorded or pack.sha256}`"]
    for a in pack.assumptions:
        out.append(f"* Assumed without proof: `{a.name}`, reviewed by {a.reviewed_by or 'nobody yet'}")
    limits = [f"{pack.budget.max_attempts} attempts" if pack.budget.max_attempts else "",
              f"{pack.budget.max_seconds_per_build:.0f} s per Lean build" if pack.budget.max_seconds_per_build else "",
              f"maxHeartbeats {pack.budget.max_heartbeats}" if pack.budget.max_heartbeats else ""]
    if any(limits):
        out.append("* Budget: " + ", ".join(x for x in limits if x))
    return out


def _source_lines(problem: Problem) -> list[str]:
    d = problem.document
    out = [f"* Source: {problem.source}"]
    if d.arxiv:
        out.append(f"* arXiv: [{d.arxiv}{d.version}](https://arxiv.org/abs/{d.arxiv}{d.version})"
                   + (f", {d.authors}" if d.authors else ""))
    elif d.authors:
        out.append(f"* Authors: {d.authors}")
    if d.url:
        out.append(f"* Link: {d.url}")
    if d.pdf_sha256:
        out.append(f"* File the claims were read from, SHA-256: `{d.pdf_sha256}`")
    return out


def _step_sections(run: Run, problem: Problem, level: str) -> list[str]:
    out: list[str] = []
    targets = {t.step.id: t for t in run.targets if t.problem.id == problem.id}
    for step in problem.steps:
        key = f"{problem.id}/{step.id}"
        title = f"{level} `{key}` {step.part}".rstrip()
        where = f" Location in the document: {step.locator}." if step.locator else ""
        t = targets.get(step.id)
        if t is None:
            why = run.meta.get("skipped", {}).get(key, "outside this run's selection")
            out += ["", title, "", f"Result: not examined in this run ({why}).{where}", "", step.informal]
            continue
        status, accepted = _status(run, t)
        statement = run.statement(t) or {}
        out += [
            "", title, "",
            f"Result: {status}.{where}", "",
            "**The claim.** " + step.informal, "",
            "Pinned Lean statement:", "",
            "```lean",
            f"theorem {step.theorem} {step.signature}",
            "```", "",
            f"* Formalised by: {step.formalised_by}",
            f"* Reviewed by: {step.reviewed_by or 'nobody yet'}",
            f"* Statement fingerprint (SHA-256): `{statement.get('statement_sha256', 'n/a')}`",
        ]
        for a in problem.assumptions_of(step):
            out += ["", f"**Named assumption `{a.name}`.** The statement takes it as a hypothesis, so what is proved "
                    f"is an implication. It stands for: {a.stands_for} Reviewed by: {a.reviewed_by or 'nobody yet'}."]
            if not a.reviewed_by:
                out += ["", f"W> Nobody has reviewed the Lean definition of `{a.name}`. If it says more than the "
                        "theorems it stands for, the result is empty; if it is false, the result is vacuous."]
        if not step.reviewed_by:
            out += ["", "W> No human has reviewed this Lean statement. The kernel checks that a proof matches the "
                    "statement; it cannot check that the statement says what the mathematics means."]
        if statement and not statement.get("valid"):
            out += ["", "The statement itself did not elaborate, so no model was asked:", "", "```text",
                    statement.get("detail", ""), "```"]
        if accepted:
            facts = accepted.get("facts") or {}
            out += ["", "**Evidence for acceptance.**", "", "| Check | Tool and criterion | Result | Seconds |",
                    "| --- | --- | --- | --- |"]
            for s in accepted["stages"]:
                out.append(f"| {s['name']} | {STAGE_TOOL.get(s['name'], '')} | "
                           f"{'passed' if s['passed'] else 'FAILED'} | {s['seconds']:.2f} |")
            out += [
                "",
                f"* Axioms the proof depends on: {', '.join(f'`{a}`' for a in facts.get('axioms', [])) or 'none'}",
                f"* Proof text: `proofs/{problem.id}/{step.id}.lean`, SHA-256 of the checked file `{accepted['attempt_sha256']}`",
                f"* Checked with: {accepted['lean_version']}" + (f", Mathlib {accepted['mathlib']}" if accepted.get("mathlib") else ""),
                "* To check it again yourself: `ztproof recheck`",
            ]
        verdicts = run.verdicts(t)
        if verdicts:
            out += ["", "**Attempt history.**", "", "| Attempt | Outcome | Reason | Files |", "| --- | --- | --- | --- |"]
            for v in verdicts:
                out.append(f"| {v['attempt']} | `{v['outcome']}` | {_cell(v['reason'])[:300]} | "
                           f"`{problem.id}/{step.id}/attempt-{v['attempt']:02d}/` |")
    return out


def render_document(run: Run, problem: Problem) -> str:
    """The referee report for one document of the run."""
    c = _tally(run, problem)
    out = [
        f"# Referee report: {problem.title}", "",
        "This report is generated from the files of one run. It states what was checked, by which tool, "
        "and what was not checked. Nothing in it comes from a model's description of its own work.", "",
        "## The document", "",
        *_source_lines(problem), *_context_lines(run, problem), "",
        f"Of {c['claims']} claim(s) pinned for this document: {c['proved']} proved, {c['not_proved']} attempted and "
        f"not proved, {c['not_formalised']} without a Lean statement, {c['other']} not examined in this run. "
        f"{c['unreviewed']} Lean statement(s) have not been reviewed by a human.", "",
        "## The run", "",
        *_run_table(run), "",
        "## The claims",
        *_step_sections(run, problem, "###"), "",
        *CAVEATS,
    ]
    return "\n".join(out)


def render(run: Run) -> str:
    """The report for the whole run: a summary across documents, then each document."""
    m = run.meta
    out: list[str] = [
        f"# Referee report for run {m['run_id']}", "",
        "This report is generated from the files in this directory. It states what was checked, by which tool, "
        "and what was not checked. Nothing in it comes from a model's description of its own work.", "",
        "## The run", "",
        *_run_table(run), "",
        "## Summary by document", "",
        "| Document | Source | Claims | Proved | Not proved | No Lean statement | Not examined | Statements unreviewed |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for problem in run.documents:
        c = _tally(run, problem)
        out.append(f"| `{problem.id}` {_cell(problem.title)} | {_cell(problem.document.cite())} | {c['claims']} | "
                   f"{c['proved']} | {c['not_proved']} | {c['not_formalised']} | {c['other']} | {c['unreviewed']} |")
    out += ["", "Each document also has its own report, `<document>/REPORT.md`, in this directory."]
    for problem in run.documents:
        out += ["", f"## `{problem.id}`: {problem.title}", "", *_source_lines(problem), *_context_lines(run, problem),
                *_step_sections(run, problem, "###")]
    out += ["", *CAVEATS]
    return "\n".join(out)
