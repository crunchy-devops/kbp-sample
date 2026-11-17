"""The glue, end to end, with a scripted stand-in for the model.

The stand-in replays fixture files. Lean and the gate are real. Runs go to a
temporary directory, never to audit/: the audit trail holds real model runs only.
"""

import json
from pathlib import Path

from conftest import needs_lean
from ztproof import gate
from ztproof.generator import Generation
from ztproof.pipeline import (GENERATION_FAILED, PROTOCOL_VIOLATION, check_statements, generate_round,
                              init_run, recheck_proofs, verify_round)
from ztproof.problem import Batch, load_problem
from ztproof.prompt import Prompt
from ztproof.report import render, render_document


class Scripted:
    model = "scripted-test-double"

    def __init__(self, replies: list[str | Generation]) -> None:
        self.replies = list(replies)
        self.prompts: list[Prompt] = []

    def generate(self, prompt: Prompt) -> Generation:
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Generation):
            return reply
        return Generation(text=reply, model_requested=self.model, model_served=self.model, stop_reason="end_turn")


def fenced(fixtures: Path, name: str) -> str:
    return "Here is my proof.\n\n```lean\n" + (fixtures / "gate" / f"{name}.lean").read_text() + "```\n"


def start(fixtures: Path, tmp_path: Path, step: str, max_attempts: int = 3):
    problem = load_problem(fixtures / "problems" / "gatecheck")
    run = init_run(problems=[problem], only=[f"gatecheck/{step}"], max_attempts=max_attempts,
                   model="scripted-test-double", effort="n/a", run_id="test-run", runs_dir=tmp_path / "runs")
    return problem, run


@needs_lean
def test_failure_feeds_the_next_attempt_until_accepted(fixtures: Path, tmp_path: Path):
    problem, run = start(fixtures, tmp_path, "sum_odds")
    assert check_statements(run)
    model = Scripted(["no code at all", fenced(fixtures, "sorry"), fenced(fixtures, "honest")])
    proofs = tmp_path / "proofs"
    rounds = 0
    while run.open_steps():
        assert generate_round(run, model) == 1
        assert verify_round(run, proofs_dir=proofs) == 1
        rounds += 1
    assert rounds == 3

    target = run.targets[0]
    assert [v["outcome"] for v in run.verdicts(target)] == [PROTOCOL_VIOLATION, gate.SORRY, gate.ACCEPTED]
    # the second and third prompts quote the previous failure
    assert "protocol_violation" in model.prompts[1].user
    assert "Verdict: `sorry`" in model.prompts[2].user and "sorry" in model.prompts[2].user

    lines = [json.loads(l) for l in (run.path / "attempts.jsonl").read_text().splitlines()]
    assert [l["attempt"] for l in lines] == [1, 2, 3]
    assert lines[2]["axioms"] is not None and lines[2]["statement_sha256"] == problem.statement_sha256(target.step)

    # the accepted proof is filed, and survives a recheck from scratch
    assert (proofs / "gatecheck" / "sum_odds.lean").is_file()
    assert [r.outcome for r in recheck_proofs([problem], proofs_dir=proofs)] == [gate.ACCEPTED]

    report = render(run)
    assert "**proved**, attempt 3 of 3" in report and "nobody yet" in report


@needs_lean
def test_attempts_are_bounded_and_failure_is_not_success(fixtures: Path, tmp_path: Path):
    _, run = start(fixtures, tmp_path, "sum_odds", max_attempts=2)
    check_statements(run)
    model = Scripted([fenced(fixtures, "axiom_cheat"), fenced(fixtures, "weaker_statement")])
    while run.open_steps():
        generate_round(run, model)
        verify_round(run, proofs_dir=tmp_path / "proofs")
    assert [v["outcome"] for v in run.verdicts(run.targets[0])] == [gate.FORBIDDEN_AXIOM, gate.STATEMENT_MISMATCH]
    assert not (tmp_path / "proofs").exists()
    assert "not proved after 2 attempt(s)" in render(run)


@needs_lean
def test_infrastructure_failure_stops_the_run_once(fixtures: Path, tmp_path: Path):
    _, run = start(fixtures, tmp_path, "sum_odds")
    check_statements(run)
    broken = Generation(text="", model_requested="m", error="authentication failed: invalid x-api-key")
    generate_round(run, Scripted([broken]))
    verify_round(run, proofs_dir=tmp_path / "proofs")
    assert run.verdicts(run.targets[0])[0]["outcome"] == GENERATION_FAILED
    assert "authentication failed" in run.meta["aborted"]
    assert run.open_steps() == []


@needs_lean
def test_recheck_notices_a_changed_statement(fixtures: Path, tmp_path: Path):
    problem, run = start(fixtures, tmp_path, "add_comm")
    check_statements(run)
    generate_round(run, Scripted([fenced(fixtures, "honest_with_lemma")]))
    verify_round(run, proofs_dir=tmp_path / "proofs")
    record = tmp_path / "proofs" / "gatecheck" / "add_comm.json"
    data = json.loads(record.read_text())
    data["statement_sha256"] = "0" * 64
    record.write_text(json.dumps(data))
    assert [r.outcome for r in recheck_proofs([problem], proofs_dir=tmp_path / "proofs")] == ["stale"]


def test_unformalised_steps_are_listed_not_attempted(fixtures: Path, tmp_path: Path):
    problem = load_problem(fixtures / "problems" / "gatecheck")
    run = init_run(problems=[problem], only=None, max_attempts=1, model="m", effort="e",
                   run_id="r", runs_dir=tmp_path / "runs")
    assert "gatecheck/not_yet" in run.meta["skipped"]
    assert "gatecheck/not_yet" not in run.meta["steps"]


@needs_lean
def test_a_batch_runs_in_parallel_and_reports_per_document(fixtures: Path, tmp_path: Path):
    core = load_problem(fixtures / "problems" / "gatecheck")
    meta = load_problem(fixtures / "problems" / "gatecheck-meta")
    batch = Batch(id="two-documents", title="Two documents", problems=("gatecheck", "gatecheck-meta"))
    run = init_run(problems=[meta, core], only=None, max_attempts=1, model="scripted-test-double", effort="n/a",
                   run_id="batch-run", runs_dir=tmp_path / "runs", batch=batch)
    assert run.meta["documents"] == ["gatecheck", "gatecheck-meta"]   # the batch's order, not the caller's
    assert [t.key for t in run.targets] == ["gatecheck/sum_odds", "gatecheck/add_comm", "gatecheck/big_decide",
                                             "gatecheck-meta/false_claim"]
    check_statements(run)

    class ByTheorem:
        """Answers by looking at which theorem the prompt asks for, so the order of calls does not matter."""
        model = "scripted-test-double"
        replies = {"sum_odds_eq_sq": "honest", "nat_add_comm'": "honest_with_lemma",
                   "count_check": "native_decide", "false_claim": "kernel_bypass"}

        def generate(self, prompt):
            name = next(k for k in self.replies if f"theorem {k} " in prompt.user)
            return Generation(text=fenced(fixtures, self.replies[name]), model_requested=self.model,
                              model_served=self.model, stop_reason="end_turn")

    assert generate_round(run, ByTheorem(), jobs=4) == 4
    assert verify_round(run, proofs_dir=tmp_path / "proofs", jobs=4) == 4
    outcomes = {t.key: run.verdicts(t)[0]["outcome"] for t in run.targets}
    assert outcomes == {"gatecheck/sum_odds": gate.ACCEPTED, "gatecheck/add_comm": gate.ACCEPTED,
                        "gatecheck/big_decide": gate.FORBIDDEN_AXIOM, "gatecheck-meta/false_claim": gate.REPLAY_FAILED}

    # the trail is written in the run's step order whatever finished first
    lines = [json.loads(l) for l in (run.path / "attempts.jsonl").read_text().splitlines()]
    assert [f"{l['problem']}/{l['step']}" for l in lines] == [t.key for t in run.targets]
    assert {l["batch"] for l in lines} == {"two-documents"}

    summary = render(run)
    assert "| `gatecheck` Gate self-test, core Lean | note | 4 | 2 | 1 | 1 | 0 | 3 |" in summary
    assert "| `gatecheck-meta` Gate self-test, metaprogramming in scope | note | 1 | 0 | 1 | 0 | 0 | 1 |" in summary
    document = render_document(run, core)
    assert "Of 4 claim(s) pinned for this document: 2 proved, 1 attempted and not proved, 1 without a Lean statement" in document
    assert "false_claim" not in document


@needs_lean
def test_a_broken_key_is_recorded_once_even_in_parallel(fixtures: Path, tmp_path: Path):
    core = load_problem(fixtures / "problems" / "gatecheck")
    run = init_run(problems=[core], only=None, max_attempts=2, model="m", effort="e",
                   run_id="broken", runs_dir=tmp_path / "runs")
    check_statements(run)
    broken = Generation(text="", model_requested="m", error="authentication failed: invalid x-api-key")
    assert generate_round(run, Scripted([broken, broken, broken]), jobs=3) == 1
    assert sum(len(run.attempt_dirs(t)) for t in run.targets) == 1
    assert run.open_steps() == []
