from pathlib import Path

import pytest

from ztproof.problem import ProblemError, load_all, load_batch, load_batches, load_problem
from ztproof.prompt import Previous, ProtocolViolation, build_prompt, extract_lean
from ztproof.pipeline import BATCHES_DIR, PROBLEMS_DIR


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
    assert {p.id for p in problems} >= {"golden-core", "golden-controls", "class-number"}
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


PREPRINT = """
id = "sample"
title = "A sample preprint"
source = "Test."
status = "open"
informal = "Text."
{document}
[[steps]]
id = "main"
locator = "Theorem 1.2, page 3"
informal = "The main theorem."
"""


def _problem(tmp_path: Path, document: str):
    target = tmp_path / "sample"
    target.mkdir(exist_ok=True)
    (target / "problem.toml").write_text(PREPRINT.format(document=document), encoding="utf-8")
    return load_problem(target)


def test_a_preprint_is_pinned_to_one_version(tmp_path: Path):
    problem = _problem(tmp_path, '[document]\nkind = "preprint"\narxiv = "2608.02859"\nversion = "v1"\nauthors = "M. Weinreich"\n')
    assert problem.document.cite() == "arXiv:2608.02859v1"
    assert problem.step("main").locator == "Theorem 1.2, page 3"


@pytest.mark.parametrize("document", [
    '[document]\nkind = "preprint"\narxiv = "2608.02859"\n',                    # which version was read?
    '[document]\nkind = "preprint"\nversion = "v1"\n',                          # which preprint?
    '[document]\nkind = "preprint"\narxiv = "not-an-id"\nversion = "v1"\n',
    '[document]\nkind = "preprint"\narxiv = "2608.02859"\nversion = "latest"\n',
    '[document]\nkind = "blog"\n',
])
def test_a_vague_document_is_refused(tmp_path: Path, document: str):
    with pytest.raises(ProblemError):
        _problem(tmp_path, document)


def test_shipped_batches_name_real_problems():
    known = {p.id for p in load_all(PROBLEMS_DIR)}
    batches = load_batches(BATCHES_DIR, known)
    assert {b.id for b in batches} >= {"golden", "worked-case"}


def test_a_batch_naming_an_unknown_problem_is_refused(tmp_path: Path):
    path = tmp_path / "b.toml"
    path.write_text('id = "b"\ntitle = "B"\nproblems = ["smoke", "missing"]\n', encoding="utf-8")
    with pytest.raises(ProblemError, match="missing"):
        load_batch(path, {"smoke"})
