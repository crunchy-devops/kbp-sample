"""Canaries: the gate seen to fail, on purpose, against the real pinned claims."""

from pathlib import Path

import pytest

from conftest import needs_lean
from ztproof import canary, gate
from ztproof.pipeline import PROBLEMS_DIR
from ztproof.problem import ProblemError, load_all


def shipped():
    problems = load_all(PROBLEMS_DIR)
    return problems, canary.load_canaries(problems)


def test_the_shipped_set_covers_every_required_move():
    _, canaries = shipped()
    assert canary.missing_moves(canaries) == []
    assert {c.problem for c in canaries} >= {"golden-core", "fermat-three"}
    # the book's exercise has its own, against its own statements
    assert {c.move for c in canaries if c.problem == "fermat-three"} >= {"sorry", "axiom", "weakened_statement",
                                                                           "added_hypothesis"}


@needs_lean
def test_every_core_lean_canary_is_rejected_for_its_own_reason():
    """The canaries on Mathlib statements need Mathlib, which CI has and a laptop may
    not; `ztproof canaries` runs all of them there. These run anywhere."""
    problems, canaries = shipped()
    core = [c for c in canaries if not next(p for p in problems if p.id == c.problem).needs_packages]
    assert len(core) >= 6
    results = canary.run_canaries(core, problems)
    assert all(r.ok for r in results), [(r.canary.key, r.state) for r in results if not r.ok]


def write(root: Path, text: str, where: str = "golden-core/sum_odds/x.lean") -> None:
    path = root / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("text, where", [
    ("theorem t : True := trivial\n", "golden-core/sum_odds/x.lean"),                        # says neither move nor expectation
    ("-- move: axiom\n-- expect: accepted\n", "golden-core/sum_odds/x.lean"),                # a canary that expects to live
    ("-- move: axiom\n-- expect: infrastructure_error\n", "golden-core/sum_odds/x.lean"),    # or to break the platform
    ("-- move: axiom\n-- expect: sorry\n", "no-such-problem/step/x.lean"),
    ("-- move: axiom\n-- expect: sorry\n", "golden-core/no-such-step/x.lean"),
])
def test_a_malformed_canary_is_an_error_not_a_skip(tmp_path: Path, text, where):
    write(tmp_path / "canaries", text, where)
    with pytest.raises(ProblemError):
        canary.load_canaries(load_all(PROBLEMS_DIR), tmp_path / "canaries")


def test_an_incomplete_set_is_reported(tmp_path: Path):
    write(tmp_path / "canaries", "-- move: sorry\n-- expect: sorry\ntheorem t : True := sorry\n")
    canaries = canary.load_canaries(load_all(PROBLEMS_DIR), tmp_path / "canaries")
    assert canary.missing_moves(canaries) == ["axiom", "native_decide", "weakened_statement", "added_hypothesis"]


@needs_lean
def test_a_canary_that_lives_and_a_canary_that_dies_of_something_else(tmp_path: Path):
    problems = load_all(PROBLEMS_DIR)
    honest = (PROBLEMS_DIR.parent / "golden" / "golden-core" / "sum_odds.lean").read_text(encoding="utf-8")
    write(tmp_path / "canaries", "-- move: sorry\n-- expect: sorry\n" + honest, "golden-core/sum_odds/alive.lean")
    write(tmp_path / "canaries", "-- move: axiom\n-- expect: forbidden_axiom\ntheorem sum_odds_eq_sq (n : Nat) : "
          "sumOdds n = n ^ 2 := by exact nonsense\n", "golden-core/sum_odds/dead.lean")
    results = {r.canary.name: r for r in canary.run_canaries(canary.load_canaries(problems, tmp_path / "canaries"), problems)}
    assert results["alive"].outcome == gate.ACCEPTED and not results["alive"].ok
    assert "the gate has a hole" in results["alive"].state
    assert results["dead"].outcome == gate.LEAN_ERROR and not results["dead"].ok
    assert results["dead"].state.startswith("DEAD")
    assert "0 of 2 rejected as they must be" in canary.render(list(results.values()), [])
