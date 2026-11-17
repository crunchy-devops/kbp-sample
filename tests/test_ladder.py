"""The training ladder: classic exercises from A-level to PhD, pinned in order."""

from pathlib import Path

from conftest import needs_lean
from ztproof import gate
from ztproof.pipeline import BATCHES_DIR, PROBLEMS_DIR
from ztproof.problem import load_all, load_batches
from ztproof.prompt import build_prompt

RUNGS = ["training-1-a-level", "training-2-first-year", "training-3-final-year", "training-4-masters", "fermat-three"]


def ladder():
    problems = {p.id: p for p in load_all(PROBLEMS_DIR)}
    batch = next(b for b in load_batches(BATCHES_DIR, set(problems)) if b.id == "training")
    return problems, batch


def test_the_batch_climbs_the_rungs_in_order():
    problems, batch = ladder()
    assert list(batch.problems) == RUNGS
    assert batch.calibration is None                     # a ladder to climb, not a gate to pass
    for number, pid in enumerate(RUNGS[:-1], start=1):
        pack = problems[pid].context
        assert pack is not None and pack.level.startswith(f"rung {number} of 5")
        assert not pack.assumptions                      # below the last rung, everything is proved in full
    assert [a.name for a in problems["fermat-three"].context.assumptions] == ["FreyLevelSix"]


def test_the_first_two_rungs_import_nothing_and_every_claim_is_pinned():
    problems, _ = ladder()
    assert [bool(problems[pid].imports) for pid in RUNGS] == [False, False, True, True, True]
    claims = [s for pid in RUNGS[:-1] for s in problems[pid].steps]
    assert len(claims) == 14 and all(s.formalised for s in claims)
    assert all(s.theorem.startswith("Ladder.") for s in claims)
    # Nobody has reviewed a statement yet, and the files must not pretend otherwise.
    assert not any(s.reviewed_by for s in claims)


def test_budgets_grow_with_the_rungs():
    problems, _ = ladder()
    attempts = [problems[pid].context.budget.max_attempts for pid in RUNGS]
    assert attempts == sorted(attempts) and attempts[0] < attempts[-1]


def test_the_prompt_tells_the_model_which_rung_it_is_on():
    problems, _ = ladder()
    problem = problems["training-1-a-level"]
    prompt = build_prompt(problem, problem.step("primes"), attempt=1, max_attempts=4,
                          lean_version="Lean (version 4.35.0)", libraries="none")
    assert "rung 1 of 5, A-level" in prompt.user
    assert "Nothing is imported" in prompt.user and "theorem Ladder.infinitely_many_primes " in prompt.user
    assert "written by people" not in prompt.user        # four packs were not, and the prompt used to say so


HONEST = """theorem Ladder.two_mul_sumTo (n : Nat) : 2 * sumTo n = n * (n + 1) := by
  induction n with
  | zero => rfl
  | succ k ih =>
    simp only [sumTo, Nat.mul_add, Nat.add_mul] at *
    omega
"""


@needs_lean
def test_an_honest_proof_of_the_first_exercise_passes_the_gate(tmp_path: Path):
    """A stand-in for the reader's own attempt: `ztproof gate` does exactly this."""
    problems, _ = ladder()
    problem = problems["training-1-a-level"]
    verdict = gate.check_attempt(problem, problem.step("triangular"), HONEST, tmp_path)
    assert verdict.outcome == gate.ACCEPTED, verdict.reason
