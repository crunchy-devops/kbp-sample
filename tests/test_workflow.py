"""The Prove workflow, read as data.

The separation it promises is checked here and not taken on trust: a step that holds
the model key runs `ztproof generate` and nothing else, and no step that runs Lean
on untrusted text ever holds the key.
"""

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
ROUNDS = 8      # as many rounds as the largest attempt budget the workflow accepts


def steps(name: str) -> list[dict]:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return [s for job in data["jobs"].values() for s in job["steps"]]


def holds_key(step: dict) -> bool:
    return "secrets." in str(step.get("env", {})) or "secrets." in str(step.get("with", {}))


def test_the_key_and_lean_never_share_a_step():
    for step in steps("prove.yml"):
        command = str(step.get("run", ""))
        if holds_key(step):
            lines = [line.strip() for line in command.splitlines() if line.strip()]
            asks_only = len(lines) == 1 and lines[0].startswith("ztproof generate ")
            assert asks_only or step.get("id") == "key", step.get("name")
        for lean_command in ("ztproof verify", "ztproof statements", "ztproof golden", "ztproof check",
                             "ztproof recheck", "ztproof calibrate", "ztproof canaries", "lake ", "lean "):
            if lean_command in command:
                assert not holds_key(step), step.get("name")


def test_the_job_level_environment_holds_no_secret():
    data = yaml.safe_load((WORKFLOWS / "prove.yml").read_text(encoding="utf-8"))
    for job in data["jobs"].values():
        assert "secrets." not in str(job.get("env", {}))


def test_there_are_as_many_rounds_as_attempts_can_be_asked_for():
    names = [str(s.get("name", "")) for s in steps("prove.yml")]
    for prefix in ("Stage 0, ", "Batch, "):
        for kind in ("ask the model", "verify in Lean"):
            assert sum(1 for n in names if n.startswith(prefix) and kind in n) == ROUNDS
    assert "1|2|3|4|5|6|7|8)" in (WORKFLOWS / "prove.yml").read_text(encoding="utf-8")


def test_stage_0_comes_before_the_batch_and_can_stop_it():
    all_steps = steps("prove.yml")
    names = [str(s.get("name", "")) for s in all_steps]
    go = names.index("Stage 0: go or no-go, and the worker pools")
    assert go < names.index("Start a run")
    # Nothing after the go/no-go step may run on failure, except the two that keep the records.
    survivors = [s.get("name") for s in all_steps[go + 1:] if "always()" in str(s.get("if", ""))]
    assert survivors == ["Keep the runs as a build artefact", "Commit the audit trail and any accepted proof"]


def test_ci_needs_no_secret_at_all():
    assert "secrets." not in (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")


def test_a_run_asked_for_by_hand_fails_when_there_is_no_key():
    """The first run a person asked for found no key, skipped every step and came out
    green. A run nobody asked for may shrug; one somebody asked for may not."""
    key_step = next(s for s in steps("prove.yml") if s.get("id") == "key")
    script = key_step["run"]
    missing = script[script.index("else"):]
    assert 'if [ "$GITHUB_EVENT_NAME" = "workflow_dispatch" ]; then' in missing
    assert "exit 1" in missing and "GITHUB_STEP_SUMMARY" in missing


def test_the_canaries_run_before_any_model_is_asked_and_in_ci():
    names = [str(s.get("name", "")) for s in steps("prove.yml")]
    canaries = next(i for i, s in enumerate(steps("prove.yml")) if "ztproof canaries" in str(s.get("run", "")))
    assert canaries < names.index("Stage 0, round 1: ask the model (holds the key, runs no Lean)")
    # recorded with the golden run, so that the calibration can refuse a go without them
    assert '--run "$RUN0"' in steps("prove.yml")[canaries]["run"]
    assert any("ztproof canaries" in str(s.get("run", "")) for s in steps("ci.yml"))
