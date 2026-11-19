"""The training ladder's book exercises, and the reference solutions in the reader's seat."""

from pathlib import Path

import pytest

from conftest import needs_lean
from ztproof import gate, solutions
from ztproof.pipeline import BATCHES_DIR, PROBLEMS_DIR
from ztproof.problem import ProblemError, load_all, load_batches
from ztproof.prompt import build_prompt

BOOKS = ["cgp-a-level-proof", "cgp-a-level-algebra", "harvard-quals-algebra"]
CGP = "d5de80252720c4081a447193b19f3ef69ba4be09903f37fcb352d91bd1d9efba"
HARVARD = "e93ba5ef439190d598e03d75f8abf60ab3309ceb7ff280797bed0f6da70d6cde"


def books():
    problems = {p.id: p for p in load_all(PROBLEMS_DIR)}
    batch = next(b for b in load_batches(BATCHES_DIR, set(problems)) if b.id == "training-books")
    return problems, batch


def test_the_batch_lists_the_book_exercises_from_a_level_to_phd():
    problems, batch = books()
    assert list(batch.problems) == BOOKS
    assert batch.calibration is None
    levels = [problems[pid].context.level for pid in BOOKS]
    assert levels[0].startswith("rung 1 of 5") and levels[1].startswith("rung 1 of 5")
    assert levels[2].startswith("rung 5 of 5")
    assert [bool(problems[pid].imports) for pid in BOOKS] == [False, True, True]


def test_every_exercise_says_where_it_comes_from_and_nobody_has_reviewed_it():
    problems, _ = books()
    for pid, sha in zip(BOOKS, (CGP, CGP, HARVARD)):
        problem = problems[pid]
        assert problem.document.kind == "exercise" and problem.document.pdf_sha256 == sha
        for step in problem.steps:
            assert step.formalised and step.theorem.startswith("Ladder.")
            assert step.locator, f"{pid}/{step.id} has no locator"
            assert not step.reviewed_by                 # and the files must not pretend otherwise
    assert sum(len(problems[pid].steps) for pid in BOOKS) == 22


def test_solve_is_pinned_as_if_and_only_if():
    """'Solve' means every solution and nothing else. The check-your-answer half is a canary."""
    problems, _ = books()
    for sid in ("linear_pair", "line_and_circle", "line_and_parabola", "quadratic_inequality"):
        assert "↔" in problems["cgp-a-level-algebra"].step(sid).signature


def test_the_prompt_never_carries_a_reference_solution():
    problems, _ = books()
    problem = problems["cgp-a-level-proof"]
    prompt = build_prompt(problem, problem.step("phyllis"), attempt=1, max_attempts=4,
                          lean_version="Lean (version 4.35.0)", libraries="none")
    assert "rung 1 of 5, A-level" in prompt.user
    assert "written_by" not in prompt.user and "Book's answer" not in prompt.user


def test_every_shipped_solution_names_a_pinned_claim_and_its_author():
    problems = load_all(PROBLEMS_DIR)
    found = solutions.load_solutions(problems)
    assert {s.key for s in found} >= {f"cgp-a-level-proof/{s.id}" for s in
                                      next(p for p in problems if p.id == "cgp-a-level-proof").steps}
    assert all(s.written_by for s in found)


def write(root: Path, text: str, where: str = "cgp-a-level-proof/phyllis.lean") -> None:
    path = root / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("where, text, message", [
    ("nowhere/phyllis.lean", "-- written_by: x\n-- reviewed_by:\n", "no problem named"),
    ("cgp-a-level-proof/nothing.lean", "-- written_by: x\n-- reviewed_by:\n", "has no step"),
    ("cgp-a-level-proof/phyllis.lean", "-- reviewed_by:\ntheorem x : True := trivial\n", "who wrote it"),
    ("cgp-a-level-proof/phyllis.lean", "-- written_by: x\ntheorem x : True := trivial\n", "who reviewed it"),
    ("golden-controls/sub_add_cancel.lean", "-- written_by: x\n-- reviewed_by:\n", "negative control"),
])
def test_a_malformed_solution_is_an_error_not_a_skip(tmp_path: Path, where: str, text: str, message: str):
    problems = load_all(PROBLEMS_DIR)
    if where.startswith("golden-controls"):
        control = next(p for p in problems if p.id == "golden-controls")
        where = f"golden-controls/{control.steps[0].id}.lean"
    write(tmp_path / "solutions", text, where)
    with pytest.raises(ProblemError, match=message):
        solutions.load_solutions(problems, tmp_path / "solutions")


@needs_lean
def test_every_core_lean_solution_passes_the_gate():
    """Mathlib solutions need Mathlib, which CI has; `ztproof solutions` runs them all there."""
    problems = load_all(PROBLEMS_DIR)
    core = [s for s in solutions.load_solutions(problems)
            if not next(p for p in problems if p.id == s.problem).needs_packages]
    assert len(core) >= 7
    results = solutions.run_solutions(core, problems)
    assert all(r.ok for r in results), [(r.solution.key, r.outcome, r.reason) for r in results if not r.ok]
    assert all(set(r.axioms) <= gate.ALLOWED_AXIOMS for r in results)


@needs_lean
def test_a_wrong_solution_is_reported_not_hidden(tmp_path: Path):
    problems = load_all(PROBLEMS_DIR)
    write(tmp_path / "solutions", "-- written_by: x\n-- reviewed_by:\n"
          "theorem Ladder.phyllis_is_wrong : ¬ ∀ n : Nat, IsPrime (2 ^ n + 1) := by\n  sorry\n")
    [result] = solutions.run_solutions(solutions.load_solutions(problems, tmp_path / "solutions"), problems)
    assert result.outcome == gate.SORRY and not result.ok
