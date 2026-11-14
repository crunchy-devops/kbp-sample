# Zero-trust AI: the proof platform

*Never trust, always verify.*

This repository is the platform behind the book *Zero-trust AI*. It takes a mathematical statement, asks a language model for a proof, and believes nothing the model says. A proof counts only when the Lean 4 kernel has accepted it for the exact statement we pinned, and every attempt that did not get that far is kept on record.

The repository is still called `kbp-sample`, for historical reasons that no longer apply.

## The pipeline

```text
              +------- GitHub Actions, glued by Python -------+
              |                                               |
 Problem ---->|  Prompt  ---->  Model  ---->  Lean 4 gate  ---|--> proofs/
 (pinned)     |    ^                              |           |
              +----|------------------------------|-----------+
                   |                              | rejected
                   |                              v
                   +---- next attempt ----  audit/runs/
```

There is no context map and no retrieval step. A prompt is built from the pinned problem and, from the second attempt on, from the previous failure. That is all the model is given.

| Stage | Where | Trusted? |
| --- | --- | --- |
| Problem and Lean statement | `problems/<id>/problem.toml` | Written and reviewed by people. The one input the kernel cannot check |
| Prompt | `prompts/v1/`, `src/ztproof/prompt.py` | Versioned text, fingerprinted in every record |
| Model call | `src/ztproof/generator.py` | No. The reply is an untrusted input |
| Gate | `lean/Gate.lean`, `src/ztproof/gate.py` | Yes, and it is short enough to read in one sitting |
| Audit trail | `audit/runs/`, `src/ztproof/pipeline.py` | Append-only by convention, in Git |
| Referee report | `src/ztproof/report.py` | Generated from the trail, never from a model |

## The gate

An attempt is accepted only if five checks pass, in this order. The first one to fail names the failure.

1. **Statement.** Our own file `ZtStatement.lean`, generated from the pinned problem, elaborates. If it does not, that is our bug and no model is asked.
2. **Compile.** The attempt elaborates without errors. Its first lines are written by the platform, so its imports are ours.
3. **Facts.** `lean/Gate.lean` loads the compiled statement and the compiled attempt as data, without running anything the attempt registered, and reports what it finds.
4. **Policy.** The attempt declares the theorem asked for; its type equals the pinned type as a kernel term; it is a theorem and not an axiom; nothing the statement mentions was redefined; and the proof depends on no axiom beyond `propext`, `Classical.choice` and `Quot.sound`. So no `sorry`, no home-made axioms and no `native_decide`.
5. **Replay.** `leanchecker` sends every declaration of the attempt through the kernel again. This catches a file that used Lean's metaprogramming to place a declaration in the environment without the kernel ever seeing it.

The policy is evaluated twice, once in Lean and once in Python, and a disagreement is itself a failure.

The test suite keeps the gate honest with fifteen fixtures: two honest proofs that must pass and thirteen that must not, among them a weakened statement, a shadowed definition, a hijacked `+`, a `sorry` hidden in a lemma and a kernel bypass that survives every check except the fifth.

## Quick start

You need Python 3.11 or later and the Lean toolchain named in `lean-toolchain`, installed with [elan](https://github.com/leanprover/elan).

```bash
pip install -e '.[dev]'
lake exe cache get        # fetches the Mathlib build; a few minutes, once

pytest -q                 # the gate against its own cheats
ztproof problems          # what is pinned, and what has been reviewed
ztproof check             # every pinned statement elaborates
ztproof recheck           # every accepted proof still passes

# One Lean file through the gate, by hand:
ztproof gate --problem smoke --step sum_odds --file my_attempt.lean

# The whole loop, locally. Needs ANTHROPIC_API_KEY and spends real tokens:
ztproof run --only smoke
```

## CI/CD

Two workflows, in `.github/workflows/`.

**CI** runs on every push and needs no secret. It runs the gate's test suite, checks that every pinned statement elaborates, and puts every accepted proof through the gate again.

**Prove** runs when a problem or a prompt changes, or on demand. It needs one repository secret, `ANTHROPIC_API_KEY`; without it the workflow says so and records nothing. With it, the workflow starts a run, alternates "ask the model" and "verify in Lean" for up to five rounds, commits the run to `audit/runs/` and any accepted proof to `proofs/`, and prints the referee report as the job summary.

The key and the untrusted Lean code never share a step. Steps that ask the model hold the key and run no Lean; steps that verify run Lean and hold no key. This is why the rounds are written out one by one in the workflow, which is less elegant than a loop and rather the point.

The model defaults to `claude-opus-5-5` at effort `high`. Set the repository variables `ZTPROOF_MODEL` and `ZTPROOF_EFFORT` to change either. If the model declines a request on policy grounds, the API may answer with a fallback model; the record then shows both the model asked and the model that answered.

## Problems

A problem is one TOML file. Each step is one link of the chain: an informal claim, and a Lean statement with a name for who formalised it and who reviewed it. A step without a Lean statement is listed and never sent to a model.

Formulas in the informal text use the manuscript's convention, a code span followed by a dollar sign, as in `n^2`$.

Facts a problem lets us assume, and earlier parts a later part relies on, enter a Lean statement as explicit hypotheses. They never enter as axioms, which the gate would reject in any case.

Two problems are pinned today.

* `smoke`: three small known results in core Lean, to prove the plumbing. If one of these fails, look at the platform before blaming the model.
* `class-number`: the book's worked case, Dirichlet's class number formula for `\mathbb{Q}(\sqrt{-p})`$ and the excess of quadratic residues, in nine steps from (a) to (e). One step has a Lean statement so far, and nobody has reviewed it.

## The audit trail and the referee report

`audit/README.md` describes the layout and every outcome. In short: for each attempt we keep the exact prompt, the exact reply, the Lean text, Lean's messages, each gate stage with its duration, the axioms found, the versions of Lean and Mathlib, and SHA-256 fingerprints of the statement and of the checked file.

Each run also gets `REPORT.md`, written for a reader who trusts neither the model nor us. For every step it gives the claim, the pinned statement, who reviewed it, the evidence for acceptance check by check, the history of attempts, and a closing section on what the run did not establish.

## What we still do not trust

* **The statements.** Lean checks that a proof matches a statement. Whether the statement says what the mathematician meant is a human judgement, and today every statement here is unreviewed.
* **Elaboration.** Compiling a Lean file can run arbitrary code. We keep the key out of those steps and give Lean a stripped environment, but there is no sandbox yet, so a hostile attempt could still interfere with the files of its own run.
* **One kernel.** Acceptance rests on Lean's reference kernel. The toolchain ships independent checkers; we do not use them yet.
* **Ourselves.** `lean/Gate.lean` and `src/ztproof/gate.py` are the trusted code. They are small, tested against cheats, and have already been wrong once; see `CHANGELOG.md`.

## Version history

`CHANGELOG.md`, failures included.
