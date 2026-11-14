from pathlib import Path

import pytest

from ztproof.problem import ProblemError, load_all, load_problem
from ztproof.prompt import Previous, ProtocolViolation, build_prompt, extract_lean
from ztproof.pipeline import PROBLEMS_DIR


def test_extract_exactly_one_block():
    assert extract_lean("Here.\n```lean\ntheorem t : True := trivial\n```\nDone.") == "theorem t : True := trivial\n"
    assert extract_lean("```lean4\nx\n```") == "x\n"


@pytest.mark.parametrize("reply", [
    "I could not prove it.",
    "```python\nprint(1)\n```",
    "```lean\na\n```\nor else\n```lean\nb\n```",
    "```lean\nnever closed",
])
def test_extract_rejects_everything_else(reply):
    with pytest.raises(ProtocolViolation):
        extract_lean(reply)


def test_prompt_names_the_pinned_statement(fixtures: Path):
    problem = load_problem(fixtures / "problems" / "gatecheck")
    step = problem.step("sum_odds")
    prompt = build_prompt(problem, step, attempt=1, max_attempts=3, lean_version="Lean 4.x", libraries="none")
    assert "theorem sum_odds_eq_sq (n : Nat) : sumOdds n = n * n := sorry" in prompt.user
    assert "def sumOdds" in prompt.user
    assert "attempt 1 of 3" in prompt.user
    assert "{{" not in prompt.user


def test_repair_prompt_carries_the_verdict_and_is_not_rescanned(fixtures: Path):
    problem = load_problem(fixtures / "problems" / "gatecheck")
    previous = Previous(attempt=1, body="theorem x : {{attempt}} := sorry", outcome="sorry",
                        reason="the proof contains `sorry`", diagnostics=["1:8: warning: declaration uses `sorry`"])
    prompt = build_prompt(problem, problem.step("sum_odds"), attempt=2, max_attempts=3,
                          lean_version="Lean 4.x", libraries="none", previous=previous)
    assert "Verdict: `sorry`" in prompt.user
    assert "theorem x : {{attempt}} := sorry" in prompt.user  # model text is never treated as a template
    assert "1:8: warning" in prompt.user


def test_same_inputs_same_fingerprint(fixtures: Path):
    problem = load_problem(fixtures / "problems" / "gatecheck")
    make = lambda: build_prompt(problem, problem.step("add_comm"), attempt=1, max_attempts=3,
                                lean_version="Lean 4.x", libraries="none")
    assert make().sha256 == make().sha256


def test_shipped_problems_load_and_are_consistent():
    problems = load_all(PROBLEMS_DIR)
    assert {p.id for p in problems} >= {"smoke", "class-number"}
    for p in problems:
        for s in p.steps:
            if s.formalised:
                assert f"theorem {s.theorem} " in p.statement_source(s)
                assert s.formalised_by


def test_unformalised_step_has_no_statement(fixtures: Path):
    problem = load_problem(fixtures / "problems" / "gatecheck")
    step = problem.step("not_yet")
    assert not step.formalised
    with pytest.raises(ProblemError):
        problem.statement_source(step)


def test_statement_fingerprint_changes_with_the_preamble(fixtures: Path, tmp_path: Path):
    src = (fixtures / "problems" / "gatecheck" / "problem.toml").read_text(encoding="utf-8")
    target = tmp_path / "gatecheck"
    target.mkdir()
    (target / "problem.toml").write_text(src.replace("2 * n + 1", "2 * n + 3"), encoding="utf-8")
    a = load_problem(fixtures / "problems" / "gatecheck")
    b = load_problem(target)
    assert a.statement_sha256(a.step("sum_odds")) != b.statement_sha256(b.step("sum_odds"))
