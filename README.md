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

Before any batch, the same pipeline is run on a golden set. This is Stage 0, described below: no go, no batch.

| Stage | Where | Trusted? |
| --- | --- | --- |
| Document, claims and Lean statements | `problems/<id>/problem.toml`, `batches/` | Written and reviewed by people. The one input the kernel cannot check |
| Prompt | `prompts/v1/`, `src/ztproof/prompt.py` | Versioned text, fingerprinted in every record |
| Model call | `src/ztproof/generator.py` | No. The reply is an untrusted input |
| Gate | `lean/Gate.lean`, `src/ztproof/gate.py` | Yes, and it is short enough to read in one sitting |
| Calibration | `batches/golden.toml`, `golden/`, `src/ztproof/calibration.py` | Measured on every run. Thresholds are set by people |
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
ztproof golden            # the golden set's reference proofs pass the gate
ztproof recheck           # every accepted proof still passes

# One Lean file through the gate, by hand:
ztproof gate --problem golden-core --step sum_odds --file my_attempt.lean

# Stage 0, locally. Needs ANTHROPIC_API_KEY and spends real tokens:
ztproof run --batch golden --run-id my-calibration
ztproof calibrate --run audit/runs/my-calibration
```

## Stage 0: calibration on a golden set

A batch of preprints is expensive, and a platform that has quietly drifted will spend the whole budget before anyone notices. So no batch starts cold. The golden batch, `batches/golden.toml`, goes first, through the same prompts, the same model and the same gate, and a short report says go or no-go.

The golden set has two halves.

* `golden-core`: five small results known to be true. Each has a reference proof in `golden/` that the gate accepts, which `ztproof golden` checks with no model at all. If a reference proof stops passing, the canary is dead and nothing measured with it means anything.
* `golden-controls`: a statement known to be **false**. It looks like a familiar identity, `(a - b) + b = a`$ on the natural numbers, and the prompt says plainly that it is a control. It must never come out proved. If it does, the gate has a hole, and that is worth more than every other measurement on the page.

`ztproof calibrate` reads the finished golden run and produces `CALIBRATION.md` and `calibration.json` in the run directory.

| Criterion | Enforced? |
| --- | --- |
| The run completed, and every golden statement elaborates | Yes |
| No false statement was accepted | Yes |
| Pass rate: share of golden claims proved within the attempts allowed | Yes, 100 % |
| Pass@1: share proved at the first attempt | Reported. No threshold until a baseline has been measured |
| Peak memory of one Lean process | Reported. No limit set yet |

A threshold is a baseline minus a tolerance. There is no baseline yet, because no real run has happened, so Pass@1 is measured and shown next to the previous calibration, and enforces nothing. Inventing a number would have been easy.

The report also sizes the two worker pools for the batch that follows, from what was measured and not from a guess:

```text
Lean checks at a time    = min(CPUs, 80 % of the machine's memory / peak memory of one Lean process)
model requests at a time = tokens-per-minute limit / tokens per minute of one worker
```

The second line needs the account's limit, given as the repository variable `ZTPROOF_TPM_LIMIT`; without it the platform sends eight requests at a time. And where nothing was measured, nothing is assumed: the golden set is core Lean only, so a batch that imports Mathlib gets its Lean checks one at a time until a golden claim imports Mathlib too.

A go is valid for one configuration: platform code and prompts, model asked, effort, attempts allowed, Lean and Mathlib builds, runner image. The report prints it with a fingerprint, and `ztproof init --calibration RUN` refuses to start a batch on a no-go or on a calibration made with another configuration.

Three things in the usual description of such a stage are not here, and the report lists them as **not evaluated**, which is not the same as passed: extracting claims from a document, a formaliser with an adversarial auditor, and containerised runners. The platform does not have those stages.

## CI/CD

Two workflows, in `.github/workflows/`.

**CI** runs on every push and needs no secret. It runs the gate's test suite, checks the golden set against its reference proofs, checks that every pinned statement elaborates, and puts every accepted proof through the gate again.

**Prove** runs when a problem, a batch, a prompt or the golden set changes, or on demand. It needs one repository secret, `ANTHROPIC_API_KEY`; without it the workflow says so and records nothing. With it, the workflow runs Stage 0 first. On a no-go the job fails there, the calibration is committed to `audit/runs/`, and no document of the batch is touched. On a go it starts a run for the batch it was given, with the worker pools Stage 0 computed, alternates "ask the model" and "verify in Lean" for up to five rounds, commits both runs to `audit/runs/` and any accepted proof to `proofs/`, and prints the calibration and the referee report as the job summary.

The key and the untrusted Lean code never share a step. Steps that ask the model hold the key and run no Lean; steps that verify run Lean and hold no key. This is why the rounds are written out one by one in the workflow, which is less elegant than a loop and rather the point.

The model defaults to `claude-opus-5-5` at effort `high`. Set the repository variables `ZTPROOF_MODEL` and `ZTPROOF_EFFORT` to change either. If the model declines a request on policy grounds, the API may answer with a fallback model; the record then shows both the model asked and the model that answered.

## Documents, claims and batches

The unit of work is a batch of documents. A document is a preprint, an exercise or a note; its claims are the steps we pin for it; and a batch is a named list of documents in `batches/`, run together.

```bash
ztproof batches                       # what is in each batch, and how far it is formalised
ztproof run --batch worked-case --jobs 4
```

One run asks the model about several claims at once and checks several replies at once. The trail is still written in a fixed order, and every document comes out with its own referee report next to a summary for the batch.

A document is described precisely enough to be found again: for a preprint, its arXiv identifier and the version that was read, since version 2 of a paper is another document; for any file, its SHA-256. Each claim can carry a locator such as "Theorem 1.2, page 3".

The platform does not read preprints yet. Claims and Lean statements are pinned by hand, and a claim nobody pinned is a claim nobody checked. The reports say so.

## Problems

A problem is one TOML file, one per document. Each step is one link of the chain: an informal claim, and a Lean statement with a name for who formalised it and who reviewed it. A step without a Lean statement is listed and never sent to a model.

Formulas in the informal text use the manuscript's convention, a code span followed by a dollar sign, as in `n^2`$.

Facts a problem lets us assume, and earlier parts a later part relies on, enter a Lean statement as explicit hypotheses. They never enter as axioms, which the gate would reject in any case.

Three problems are pinned today.

* `golden-core`: five small known results in core Lean, the positive half of the golden set. If one of these fails, look at the platform before blaming the model.
* `golden-controls`: one false statement, the negative half. A run that names no batch leaves both golden problems out: canaries are not results.
* `class-number`: the book's worked case, Dirichlet's class number formula for `\mathbb{Q}(\sqrt{-p})`$ and the excess of quadratic residues, in nine steps from (a) to (e). One step has a Lean statement so far, and nobody has reviewed it.

## The audit trail and the referee report

`audit/README.md` describes the layout and every outcome. In short: for each attempt we keep the exact prompt, the exact reply, the Lean text, Lean's messages, each gate stage with its duration, the axioms found, the versions of Lean and Mathlib, and SHA-256 fingerprints of the statement and of the checked file.

Each run also gets `REPORT.md`, written for a reader who trusts neither the model nor us. It opens with a summary by document: claims pinned, proved, attempted and not proved, without a Lean statement, and how many statements no human has reviewed. Then, for every claim, it gives the informal text and where it sits in the document, the pinned statement, who reviewed it, the evidence for acceptance check by check, the history of attempts, and a closing section on what the run did not establish. Each document has the same report on its own, in `<run>/<document>/REPORT.md`.

A run cleared by Stage 0 names the calibration in its report. A calibration run files nothing under `proofs/`.

## What we still do not trust

* **The statements.** Lean checks that a proof matches a statement. Whether the statement says what the mathematician meant is a human judgement, and today every statement here is unreviewed.
* **Elaboration.** Compiling a Lean file can run arbitrary code. We keep the key out of those steps and give Lean a stripped environment, but there is no sandbox yet, so a hostile attempt could still interfere with the files of its own run.
* **One kernel.** Acceptance rests on Lean's reference kernel. The toolchain ships independent checkers; we do not use them yet.
* **The golden set.** Five easy results and one false statement say that the plumbing works and that one particular hole is closed. They say little about hard claims, and nothing about Mathlib. A go means "not obviously broken", never "correct".
* **Ourselves.** `lean/Gate.lean` and `src/ztproof/gate.py` are the trusted code. They are small, tested against cheats, and have already been wrong once; see `CHANGELOG.md`.

## Version history

`CHANGELOG.md`, failures included.
