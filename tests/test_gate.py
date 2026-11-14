"""The gate against honest proofs and against every cheat we could think of.

Each fixture in tests/fixtures/gate/ is a Lean text of the kind a model might send.
These tests run the real toolchain: nothing here is mocked.
"""

from pathlib import Path

import pytest

from conftest import needs_lean
from ztproof import gate
from ztproof.problem import load_problem

CASES = [
    # fixture, problem, step, expected outcome
    ("honest", "gatecheck", "sum_odds", gate.ACCEPTED),
    ("honest_with_lemma", "gatecheck", "add_comm", gate.ACCEPTED),
    ("sorry", "gatecheck", "sum_odds", gate.SORRY),
    ("sorry_in_lemma", "gatecheck", "sum_odds", gate.SORRY),
    ("lean_error", "gatecheck", "sum_odds", gate.LEAN_ERROR),
    ("import_line", "gatecheck", "add_comm", gate.LEAN_ERROR),
    ("wrong_name", "gatecheck", "sum_odds", gate.THEOREM_MISSING),
    ("weaker_statement", "gatecheck", "sum_odds", gate.STATEMENT_MISMATCH),
    ("extra_hypothesis", "gatecheck", "sum_odds", gate.STATEMENT_MISMATCH),
    ("shadowed_definition", "gatecheck", "sum_odds", gate.STATEMENT_MISMATCH),
    ("hijacked_notation", "gatecheck", "add_comm", gate.STATEMENT_MISMATCH),
    ("declared_as_axiom", "gatecheck", "sum_odds", gate.NOT_A_THEOREM),
    ("axiom_cheat", "gatecheck", "sum_odds", gate.FORBIDDEN_AXIOM),
    ("native_decide", "gatecheck", "big_decide", gate.FORBIDDEN_AXIOM),
    ("kernel_bypass", "gatecheck-meta", "false_claim", gate.REPLAY_FAILED),
]


@needs_lean
@pytest.mark.parametrize("fixture,problem_id,step_id,expected", CASES, ids=[c[0] for c in CASES])
def test_gate_verdict(fixtures: Path, tmp_path: Path, fixture, problem_id, step_id, expected):
    problem = load_problem(fixtures / "problems" / problem_id)
    body = (fixtures / "gate" / f"{fixture}.lean").read_text(encoding="utf-8")
    verdict = gate.check_attempt(problem, problem.step(step_id), body, tmp_path)
    assert verdict.outcome == expected, verdict.reason
    assert verdict.accepted == (expected == gate.ACCEPTED)


def test_every_fixture_has_a_case(fixtures: Path):
    on_disk = {p.stem for p in (fixtures / "gate").glob("*.lean")}
    assert on_disk == {c[0] for c in CASES}


@needs_lean
def test_kernel_bypass_passes_everything_but_the_replay(fixtures: Path, tmp_path: Path):
    """The point of stage 5: this file compiles, matches the statement and shows clean
    axioms. Only replaying it through the kernel exposes it."""
    problem = load_problem(fixtures / "problems" / "gatecheck-meta")
    body = (fixtures / "gate" / "kernel_bypass.lean").read_text(encoding="utf-8")
    verdict = gate.check_attempt(problem, problem.step("false_claim"), body, tmp_path)
    passed = {s.name: s.passed for s in verdict.stages}
    assert passed == {"statement": True, "compile": True, "facts": True, "policy": True, "replay": False}


@needs_lean
def test_diagnostics_are_numbered_from_the_body(fixtures: Path, tmp_path: Path):
    problem = load_problem(fixtures / "problems" / "gatecheck")
    body = (fixtures / "gate" / "lean_error.lean").read_text(encoding="utf-8")
    verdict = gate.check_attempt(problem, problem.step("sum_odds"), body, tmp_path)
    assert verdict.diagnostics[0].startswith("2:2: error")


def test_policy_ignores_the_gate_programs_own_ok():
    facts = {"theorem": "t", "statement_found": True, "attempt_found": True, "type_match": True,
             "kind": "theorem", "redefined": [], "missing": [], "axioms": ["propext", "sorryAx"], "ok": True}
    assert gate.apply_policy(facts)[0] == gate.SORRY


@pytest.mark.parametrize("change,expected", [
    ({}, gate.ACCEPTED),
    ({"attempt_found": False}, gate.THEOREM_MISSING),
    ({"type_match": False}, gate.STATEMENT_MISMATCH),
    ({"kind": "def"}, gate.NOT_A_THEOREM),
    ({"redefined": ["Nat.Prime"]}, gate.REDEFINITION),
    ({"missing": ["Nat.Prime"]}, gate.REDEFINITION),
    ({"axioms": ["Lean.ofReduceBool"]}, gate.FORBIDDEN_AXIOM),
    ({"axioms": ["propext", "Classical.choice", "Quot.sound"]}, gate.ACCEPTED),
])
def test_policy_table(change, expected):
    facts = {"theorem": "t", "statement_found": True, "attempt_found": True, "type_match": True,
             "kind": "theorem", "redefined": [], "missing": [], "axioms": []}
    facts.update(change)
    assert gate.apply_policy(facts)[0] == expected
