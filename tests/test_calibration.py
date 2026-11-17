"""Stage 0, the calibration gate, with a scripted stand-in for the model.

The golden set is the shipped one. Lean and the gate are real. As everywhere in
the tests, runs go to a temporary directory: the audit trail holds real runs only.
"""

import json
from pathlib import Path

import pytest

from conftest import needs_lean
from ztproof import calibration, cli, gate
from ztproof.generator import Generation
from ztproof.pipeline import (BATCHES_DIR, GOLDEN_DIR, PROBLEMS_DIR, check_statements, generate_round, init_run,
                              verify_round)
from ztproof.problem import ProblemError, load_all, load_batch, load_batches, load_problem
from ztproof.report import render


def golden():
    problems = load_all(PROBLEMS_DIR)
    batch = next(b for b in load_batches(BATCHES_DIR, {p.id for p in problems}) if b.id == "golden")
    return problems, batch


class Replay:
    """Answers each golden claim with its reference proof, except where told otherwise."""
    model = "scripted-test-double"

    def __init__(self, problems, instead: dict[str, str] | None = None) -> None:
        self.by_theorem = {s.theorem: (p.id, s.id) for p in problems for s in p.steps if s.formalised}
        self.instead = instead or {}

    def generate(self, prompt):
        theorem = next(k for k in self.by_theorem if f"theorem {k} " in prompt.user)
        pid, sid = self.by_theorem[theorem]
        reference = GOLDEN_DIR / pid / f"{sid}.lean"
        text = self.instead.get(sid)
        if text is None:
            text = ("```lean\n" + reference.read_text() + "\n```\n" if reference.is_file()
                    else "The statement is false: take a = 0 and b = 1. I give no Lean block.")
        return Generation(text=text, model_requested=self.model, model_served=self.model, stop_reason="end_turn",
                          usage={"input_tokens": 900, "output_tokens": 300}, seconds=6.0)


def play(run, model, tmp_path: Path) -> None:
    check_statements(run)
    while run.open_steps():
        generate_round(run, model, jobs=4)
        verify_round(run, proofs_dir=tmp_path / "proofs", jobs=2)


def start(tmp_path: Path, only=None, max_attempts=2, **more):
    problems, batch = golden()
    run = init_run(problems=problems, only=only, max_attempts=max_attempts, model="scripted-test-double",
                   effort="n/a", run_id="golden-run", runs_dir=tmp_path / "runs", batch=batch, **more)
    return problems, run


@needs_lean
def test_the_reference_proofs_pass_the_gate_with_no_model():
    problems, batch = golden()
    results = calibration.check_references(batch, {p.id: p for p in problems})
    assert results and all(r.ok for r in results), [(r.key, r.outcome, r.detail) for r in results if not r.ok]
    assert {r.outcome for r in results} == {gate.ACCEPTED, "control_ready"}


@needs_lean
def test_a_missing_reference_proof_is_a_dead_canary(tmp_path: Path):
    problems, batch = golden()
    results = calibration.check_references(batch, {p.id: p for p in problems}, golden_dir=tmp_path / "empty")
    assert [r.outcome for r in results if not r.ok] == ["no_reference"] * 5


@needs_lean
def test_a_clean_golden_run_is_a_go(tmp_path: Path, monkeypatch):
    problems, run = start(tmp_path)
    assert run.meta["calibration"] == {"min_pass_rate": 1.0}
    play(run, Replay(problems), tmp_path)
    result = calibration.calibrate(run, tpm_limit=40_000)
    m = result.metrics
    assert result.go
    assert (m["claims"], m["proved"], m["proved_first_attempt"], m["controls"], m["controls_accepted"]) == (5, 5, 5, 1, [])
    assert m["outcomes"] == {gate.ACCEPTED: 5, "protocol_violation": 2}      # the control used both its attempts
    assert m["peak_lean_mb"] > 100 and m["gate_seconds_max"] > 0
    assert m["tokens_per_minute_per_worker"] == 12_000                       # 1200 tokens every 6 seconds
    assert result.sizing["generate_jobs"] == 3                               # 40 000 over 12 000
    assert result.sizing["verify_jobs"] >= 1
    by_name = {c.name: c.passed for c in result.criteria}
    assert by_name["Pass rate"] is True and by_name["No false statement was accepted"] is True
    assert by_name["Pass@1"] is None                                         # measured, and no threshold to enforce

    calibration.save(run, result)
    text = (run.path / "CALIBRATION.md").read_text()
    assert text.startswith("# Stage 0 calibration: GO")
    assert "reported, not enforced" in text and "## Not evaluated" in text
    assert json.loads((run.path / "calibration.json").read_text())["configuration_sha256"] == result.configuration_sha256
    # a calibration run files nothing under proofs/
    assert not (tmp_path / "proofs").exists()
    assert "which is the expected result" in render(run)

    # the command CI runs: the same verdict, handed to the next step
    handover = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(handover))
    assert cli.main(["calibrate", "--run", str(run.path), "--next-batch", "worked-case"]) == 0
    assert handover.read_text().splitlines() == ["go=true", "verify_jobs=1", "generate_jobs=8"]   # Mathlib: not measured


@needs_lean
def test_a_golden_claim_left_unproved_is_a_no_go(tmp_path: Path):
    problems, run = start(tmp_path, only=["golden-core/sub_add_cancel", "golden-core/half_double_le"])
    sorry = "```lean\ntheorem half_double_le (n : Nat) : n / 2 * 2 ≤ n := sorry\n```\n"
    play(run, Replay(problems, instead={"half_double_le": sorry}), tmp_path)
    result = calibration.calibrate(run)
    assert not result.go
    assert result.metrics["pass_rate"] == 0.5 and result.metrics["not_proved"] == ["golden-core/half_double_le"]
    assert {c.name for c in result.criteria if c.passed is False} == {"Pass rate"}
    assert calibration.render(result).startswith("# Stage 0 calibration: NO-GO")
    assert cli.main(["calibrate", "--run", str(run.path)]) == 1          # the step fails, and the job stops there


CONTROL_THAT_IS_TRUE = '''
id = "leaky"
title = "A control that is in fact true, to stand in for a hole in the gate"
source = "test"
status = "control"
informal = "x"

[[steps]]
id = "zero"
informal = "x"
theorem = "add_zero_again"
signature = "(n : Nat) : n + 0 = n"
formalised_by = "test"
expect = "not_proved"
'''


@needs_lean
def test_an_accepted_control_is_a_no_go_and_is_not_filed_as_a_proof(tmp_path: Path):
    (tmp_path / "problems" / "leaky").mkdir(parents=True)
    (tmp_path / "problems" / "leaky" / "problem.toml").write_text(CONTROL_THAT_IS_TRUE, encoding="utf-8")
    problem = load_problem(tmp_path / "problems" / "leaky")
    (tmp_path / "b.toml").write_text('id = "b"\ntitle = "B"\nproblems = ["leaky"]\n[calibration]\n', encoding="utf-8")
    batch = load_batch(tmp_path / "b.toml", {"leaky"})
    run = init_run(problems=[problem], only=None, max_attempts=1, model="m", effort="e", run_id="leak",
                   runs_dir=tmp_path / "runs", batch=batch)

    class Prover:
        def generate(self, prompt):
            assert "calibration control" in prompt.user          # the model is told the truth
            return Generation(text="```lean\ntheorem add_zero_again (n : Nat) : n + 0 = n := rfl\n```\n",
                              model_requested="m", model_served="m", stop_reason="end_turn")

    play(run, Prover(), tmp_path)
    result = calibration.calibrate(run)
    assert not result.go and result.metrics["controls_accepted"] == ["leaky/zero"]
    assert not (tmp_path / "proofs").exists()
    assert "ACCEPTED, AND IT MUST NOT BE" in render(run)


def test_an_ordinary_run_cannot_be_calibrated(fixtures: Path, tmp_path: Path):
    problem = load_problem(fixtures / "problems" / "gatecheck")
    run = init_run(problems=[problem], only=None, max_attempts=1, model="m", effort="e", run_id="plain",
                   runs_dir=tmp_path / "runs")
    with pytest.raises(ProblemError, match="not a calibration run"):
        calibration.calibrate(run)


def test_worker_pools_follow_the_measurements():
    host = {"ram_mb": 16_000, "cpus": 4}
    measured = {"peak_lean_mb": 2300.0, "tokens_per_minute_per_worker": 12_000}
    sized = calibration.size_workers(measured, host, tpm_limit=90_000)
    assert (sized["verify_jobs"], sized["generate_jobs"], sized["max_workers"]) == (4, 7, 4)   # 5 by memory, 4 CPUs
    assert calibration.size_workers({**measured, "peak_lean_mb": 9000.0}, host, 90_000)["verify_jobs"] == 1
    # nothing measured, nothing assumed
    blind = calibration.size_workers({"peak_lean_mb": None, "tokens_per_minute_per_worker": None}, host, None)
    assert blind["verify_jobs"] == 1 and blind["generate_jobs"] == calibration.DEFAULT_GENERATE_JOBS
    # a library the golden set never loaded: one Lean check at a time
    assert calibration.size_workers(measured, host, 90_000, unmeasured=["Mathlib"])["verify_jobs"] == 1


@needs_lean
def test_a_go_clears_only_the_configuration_it_measured(tmp_path: Path):
    problems, run = start(tmp_path, only=["golden-core/sub_add_cancel"])
    play(run, Replay(problems), tmp_path)
    result = calibration.calibrate(run, next_imports=["Mathlib"])
    assert result.go and result.sizing["verify_jobs"] == 1
    assert any("Mathlib" in n["criterion"] for n in result.not_evaluated)
    calibration.save(run, result)

    same = dict(model="scripted-test-double", effort="n/a", max_attempts=2, runs_dir=tmp_path / "runs")
    cleared = calibration.clearance("golden-run", **same)
    assert cleared["configuration_sha256"] == result.configuration_sha256
    with pytest.raises(ProblemError, match="model_requested"):
        calibration.clearance("golden-run", **{**same, "model": "another-model"})
    with pytest.raises(ProblemError, match="no calibration on file"):
        calibration.clearance("never-ran", **same)

    data = json.loads((run.path / "calibration.json").read_text())
    data["go"] = False
    (run.path / "calibration.json").write_text(json.dumps(data))
    with pytest.raises(ProblemError, match="no-go"):
        calibration.clearance("golden-run", **same)


@needs_lean
def test_a_canary_that_lived_or_died_of_something_else_is_a_no_go(tmp_path: Path):
    problems, run = start(tmp_path, only=["golden-core/sub_add_cancel"])
    play(run, Replay(problems), tmp_path)
    name = "Every canary was rejected, and for its own reason"

    def verdict():
        result = calibration.calibrate(run)
        return result.go, next(c for c in result.criteria if c.name == name)

    go, criterion = verdict()
    assert go and criterion.passed is None and "not run" in criterion.detail     # reported as missing, not as passed

    row = {"canary": "golden-core/sum_odds/axiom", "move": "axiom", "expect": "forbidden_axiom",
           "outcome": "forbidden_axiom", "ok": True, "reason": "", "seconds": 1.0}
    record = run.path / "canaries.json"
    record.write_text(json.dumps({"complete": True, "missing_moves": [], "results": [row]}))
    go, criterion = verdict()
    assert go and criterion.passed is True

    record.write_text(json.dumps({"complete": True, "missing_moves": [],
                                  "results": [{**row, "outcome": "accepted", "ok": False}]}))
    go, criterion = verdict()
    assert not go and criterion.passed is False and "golden-core/sum_odds/axiom" in criterion.detail

    record.write_text(json.dumps({"complete": False, "missing_moves": ["native_decide"], "results": [row]}))
    go, criterion = verdict()
    assert not go and "no canary for: native_decide" in criterion.detail


BASE = 'id = "p"\ntitle = "P"\nsource = "s"\nstatus = "{status}"\ninformal = "x"\n[[steps]]\nid = "a"\ninformal = "x"\n' \
       'theorem = "t"\nsignature = ": True"\nformalised_by = "me"\nexpect = "{expect}"\n'


@pytest.mark.parametrize("status, expect, ok", [
    ("control", "not_proved", True), ("known", "proved", True),
    ("known", "not_proved", False),          # a false statement hiding among real claims
    ("control", "maybe", False),
])
def test_only_a_control_problem_may_hold_a_false_statement(tmp_path: Path, status, expect, ok):
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "problem.toml").write_text(BASE.format(status=status, expect=expect), encoding="utf-8")
    if ok:
        assert load_problem(tmp_path / "p").step("a").expect == expect
    else:
        with pytest.raises(ProblemError):
            load_problem(tmp_path / "p")


@pytest.mark.parametrize("table", ['min_pass_rate = 1.5', 'min_pass_at_1 = "high"', 'max_tokens = 3', 'min_pass_rate = true'])
def test_calibration_thresholds_are_validated(tmp_path: Path, table: str):
    path = tmp_path / "b.toml"
    path.write_text(f'id = "b"\ntitle = "B"\nproblems = ["p"]\n[calibration]\n{table}\n', encoding="utf-8")
    with pytest.raises(ProblemError):
        load_batch(path, {"p"})
