"""The context pack: what a model may be told, checked against everything it can be checked against."""

import json
import shutil
from pathlib import Path

import pytest

from conftest import needs_lean
from test_pipeline import Scripted, fenced
from ztproof import gate
from ztproof.pipeline import PROBLEMS_DIR, check_statements, generate_round, init_run, verify_round
from ztproof.problem import ProblemError, load_problem
from ztproof.prompt import build_prompt
from ztproof.report import render


def packed(fixtures: Path, tmp_path: Path, *, pack=None, problem=None) -> Path:
    """A copy of the test problem, with its pack or its problem file rewritten."""
    target = tmp_path / "packed"
    shutil.copytree(fixtures / "problems" / "packed", target)
    for name, change in (("context.yaml", pack), ("problem.toml", problem)):
        if change:
            text = (target / name).read_text(encoding="utf-8")
            assert change[0] in text
            (target / name).write_text(text.replace(*change), encoding="utf-8")
    return target


def test_the_pack_is_loaded_and_fingerprinted(fixtures: Path):
    problem = load_problem(fixtures / "problems" / "packed")
    pack = problem.context
    assert pack.library == "Packed" and len(pack.sha256) == 64
    assert [a.name for a in problem.assumptions_of(problem.step("conditional"))] == ["DeepFact"]
    assert pack.assumptions[0].reviewed_by == ""          # a placeholder between angle brackets is not a review
    assert problem.attempts_allowed(5) == 2 and problem.attempts_allowed(1) == 1


@pytest.mark.parametrize("change", [
    ("leanprover/lean4:v4.35.0", "leanprover/lean4:v4.34.1"),      # not the toolchain this repository pins
    ("imports: []", "imports: [Mathlib]"),                         # not what the problem imports
    ("id: packed", "id: something-else"),
    ("forbidden:", "forbiden:"),                                   # a misspelt rule is a rule nobody enforces
    ("name: DeepFact", "name: ShallowFact"),                       # an assumption defined nowhere
    ("max_attempts: 2", "max_attempts: 0"),
    ("max_heartbeats: 200000", "max_heartbeats: plenty"),
    ("how_to_use:", "how_to_abuse:"),
], ids=lambda c: c[1])
def test_a_pack_that_contradicts_the_platform_is_refused(fixtures: Path, tmp_path: Path, change):
    with pytest.raises(ProblemError):
        load_problem(packed(fixtures, tmp_path, pack=change))


def test_an_assumption_must_be_a_hypothesis_of_a_pinned_statement(fixtures: Path, tmp_path: Path):
    with pytest.raises(ProblemError, match="hypothesis of no pinned statement"):
        load_problem(packed(fixtures, tmp_path, problem=("(h : DeepFact) (n : Nat)", "(n : Nat)")))


def test_a_problem_may_not_declare_an_axiom(fixtures: Path, tmp_path: Path):
    change = ("def DeepFact : Prop := ∀ n : Nat, n ≤ 2 * n",
              "def DeepFact : Prop := ∀ n : Nat, n ≤ 2 * n\naxiom deepFact : DeepFact")
    with pytest.raises(ProblemError, match="never as an axiom"):
        load_problem(packed(fixtures, tmp_path, problem=change))


def test_a_mathlib_pack_must_name_the_pinned_mathlib(fixtures: Path, tmp_path: Path):
    target = packed(fixtures, tmp_path, pack=("imports: []", "imports: [Mathlib]"),
                    problem=("imports = []", 'imports = ["Mathlib"]'))
    load_problem(target)                                           # v4.35.0 in the pack, v4.35.0 in the manifest
    text = (target / "context.yaml").read_text(encoding="utf-8")
    (target / "context.yaml").write_text(text.replace("mathlib: v4.35.0", "mathlib: v4.34.1"), encoding="utf-8")
    with pytest.raises(ProblemError, match="lake-manifest.json"):
        load_problem(target)


def test_the_prompt_carries_the_pack_and_only_when_there_is_one(fixtures: Path):
    problem = load_problem(fixtures / "problems" / "packed")
    prompt = build_prompt(problem, problem.step("conditional"), attempt=1, max_attempts=2,
                          lean_version="Lean 4.x", libraries="none")
    assert prompt.version == "v2"
    for expected in ("# Context pack", "`DeepFact`: A deep theorem", "Never as an axiom", "the deep theorem itself",
                     "decide +native", "`maxHeartbeats` is fixed at 200000", "stopped after 120 seconds"):
        assert expected in prompt.user, expected
    plain = load_problem(fixtures / "problems" / "gatecheck")
    assert "# Context pack" not in build_prompt(plain, plain.step("sum_odds"), attempt=1, max_attempts=3,
                                                lean_version="Lean 4.x", libraries="none").user


def test_the_shipped_exercise_has_its_pack_and_its_named_assumption():
    problem = load_problem(PROBLEMS_DIR / "fermat-three")
    assert problem.context.library == "Fermat3"
    assert [a.name for a in problem.assumptions_of(problem.step("main"))] == ["FreyLevelSix"]
    assert problem.assumptions_of(problem.step("frey")) == []      # the reduction is unconditional
    assert problem.context.assumptions[0].reviewed_by == ""        # and nobody has reviewed the assumption yet
    assert problem.attempts_allowed(8) == 8


@needs_lean
def test_the_budget_caps_the_attempts_and_the_fingerprint_is_on_every_record(fixtures: Path, tmp_path: Path):
    problem = load_problem(fixtures / "problems" / "packed")
    run = init_run(problems=[problem], only=None, max_attempts=5, model="scripted-test-double", effort="n/a",
                   run_id="packed-run", runs_dir=tmp_path / "runs")
    assert run.meta["context_packs"] == {"packed": problem.context.sha256}
    check_statements(run)
    model = Scripted([fenced(fixtures, "packed_set_heartbeats"), fenced(fixtures, "assumption_as_axiom"),
                      fenced(fixtures, "packed_honest")])
    while run.open_steps():
        generate_round(run, model)
        verify_round(run, proofs_dir=tmp_path / "proofs")
    target = run.targets[0]
    # five attempts allowed by the run, two by the pack: the honest proof is never asked for
    assert [v["outcome"] for v in run.verdicts(target)] == [gate.BUDGET_OVERRIDE, gate.FORBIDDEN_AXIOM]
    assert "attempt 1 of 2" in model.prompts[0].user
    assert "sets `maxHeartbeats`" in model.prompts[1].user          # the refusal is fed to the next attempt
    lines = [json.loads(l) for l in (run.path / "attempts.jsonl").read_text().splitlines()]
    assert {l["context_sha256"] for l in lines} == {problem.context.sha256}
    assert {l["max_attempts"] for l in lines} == {2}
    assert f"SHA-256 `{problem.context.sha256}`" in render(run)


@needs_lean
def test_a_proof_under_a_named_assumption_is_reported_as_conditional(fixtures: Path, tmp_path: Path):
    problem = load_problem(fixtures / "problems" / "packed")
    run = init_run(problems=[problem], only=None, max_attempts=1, model="scripted-test-double", effort="n/a",
                   run_id="conditional", runs_dir=tmp_path / "runs")
    check_statements(run)
    generate_round(run, Scripted([fenced(fixtures, "packed_honest")]))
    verify_round(run, proofs_dir=tmp_path / "proofs")
    report = render(run)
    assert "**proved, assuming `DeepFact`**, attempt 1 of 1" in report
    assert "Nobody has reviewed the Lean definition of `DeepFact`" in report
    statement = json.loads((run.step_dir(run.targets[0]) / "statement.json").read_text())
    assert statement["assumptions"] == [{"name": "DeepFact", "reviewed_by": ""}]


@needs_lean
def test_a_pack_changed_during_a_run_stops_the_run(fixtures: Path, tmp_path: Path):
    target = packed(fixtures, tmp_path)
    run = init_run(problems=[load_problem(target)], only=None, max_attempts=2, model="m", effort="e",
                   run_id="moved", runs_dir=tmp_path / "runs")
    check_statements(run)
    text = (target / "context.yaml").read_text(encoding="utf-8")
    (target / "context.yaml").write_text(text.replace("Natural numbers are Nat.", "Psst: the answer is rfl."),
                                         encoding="utf-8")
    from ztproof.pipeline import Run
    later = Run(run.path, [load_problem(target)])
    assert generate_round(later, Scripted([])) == 0
    assert "context pack of packed changed" in later.meta["aborted"]
