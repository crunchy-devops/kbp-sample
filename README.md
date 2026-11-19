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

There is no context map and no retrieval step. A prompt is built from the pinned problem, from the problem's context pack if it has one, and, from the second attempt on, from the previous failure. That is all the model is given, and all of it is on file.

Before any batch, the same pipeline is run on a golden set. This is Stage 0, described below: no go, no batch.

| Stage | Where | Trusted? |
| --- | --- | --- |
| Document, claims and Lean statements | `problems/<id>/problem.toml`, `batches/` | Pinned by hand before a run. Each statement names who wrote it and who reviewed it; today none has been reviewed. The one input the kernel cannot check |
| Context pack | `problems/<id>/context.yaml`, `src/ztproof/context.py` | Written before a run, and checked against the repository's own pins. What it forbids is enforced by the gate, not by the model's goodwill |
| Prompt | `prompts/v2/`, `src/ztproof/prompt.py` | Versioned text, fingerprinted in every record |
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

Where a context pack sets a budget, one check comes before Lean runs: an attempt that sets `maxHeartbeats` itself is refused as `budget_override`.

The test suite keeps the gate honest with twenty-one fixtures: four that must pass and seventeen that must not, among them a weakened statement, a shadowed definition, a hijacked `+`, a `sorry` hidden in a lemma, an `admit`, a `decide +native`, a named assumption promoted to an axiom, and a kernel bypass that survives every check except the fifth.

## Canaries

A gate that has never been seen to fail cannot be trusted. The fixtures above test the gate's code, against toy statements, when somebody runs the tests. The `canaries/` folder does something else: it holds submissions written against the **real pinned claims**, each making one forbidden move, and every one of them goes through the gate in CI and at the start of every Prove run, on the machine that is about to do the run and before any model is asked anything.

A canary is one Lean file, exactly what a model would have sent, at `canaries/<problem>/<step>/<name>.lean`. Its first comments name the move it makes and the rejection it must get:

```lean
-- move: axiom
-- expect: forbidden_axiom
-- Declares the conclusion and uses it. It compiles without an error.
namespace Fermat3

axiom cheat (p : ℕ) (x y z : ℤ) : x ^ p + y ^ p ≠ 3 * z ^ p

theorem fermat_three (hFrey : FreyLevelSix) (p : ℕ) (hp : p.Prime) (hp5 : 5 ≤ p) (x y z : ℤ)
    (hx : x ≠ 0) (hy : y ≠ 0) (hz : z ≠ 0) (hgcd : Int.gcd (Int.gcd x y) z = 1) :
    x ^ p + y ^ p ≠ 3 * z ^ p := cheat p x y z

end Fermat3
```

That file compiles, so the second check lets it through. The fourth stops it, because `Fermat3.cheat` is not one of the three axioms allowed.

`ztproof canaries` runs them all, with no model and no key, and prints one line per canary. There are three possible results, and only the first is good news.

| Result | Meaning |
| --- | --- |
| Rejected, with the outcome named in `-- expect:` | The canary died as it should |
| Accepted | The gate has a hole. Stop everything |
| Rejected, with another outcome | A dead canary. A cheat that no longer compiles tests Lean's parser, not the gate, so it counts as a failure too |

The command fails on either of the last two. It also fails when the folder as a whole lacks one of the five moves every set must hold: `sorry`, `axiom`, `native_decide`, `weakened_statement`, `added_hypothesis`. Other moves are welcome under any name.

| Canary | Move | Must be rejected as |
| --- | --- | --- |
| `fermat-three/main/sorry` | The exercise, left as an exercise | `sorry` |
| `fermat-three/main/axiom` | The conclusion declared as an axiom, then used | `forbidden_axiom` |
| `fermat-three/main/weakened_statement` | Same name and hypotheses, conclusion `True` | `statement_mismatch` |
| `fermat-three/main/added_hypothesis` | One more hypothesis: that there is no solution | `statement_mismatch` |
| `fermat-three/frey/axiom` | The hard step, the Frey triple, declared and not built | `forbidden_axiom` |
| `golden-core/sum_odds/sorry`, `axiom`, `weakened_statement`, `added_hypothesis` | The same four moves on a core Lean claim | as above |
| `golden-core/excess_23/native_decide`, `decide_native` | A true fact checked by compiled code the kernel never sees | `forbidden_axiom` |
| `golden-core/sub_add_cancel/declared_as_axiom` | The claim itself declared as an axiom | `not_a_theorem` |
| `training-1-a-level/primes/axiom` | Euclid's theorem declared, then cited | `forbidden_axiom` |
| `training-1-a-level/sqrt_two/weakened_statement` | Same hypothesis, a conclusion true of every number | `statement_mismatch` |
| `training-2-first-year/euclid_lemma/added_hypothesis` | Euclid's lemma, assuming the conclusion | `statement_mismatch` |
| `training-2-first-year/pigeonhole/sorry` | The pigeons left to sort themselves out | `sorry` |

To add a canary:

1. Write the Lean text a cheating model would send for one pinned claim. `ztproof prompt --problem ID --step STEP` shows the exact statement to copy.
2. Put `-- move:` and `-- expect:` at the top. The move is a short name in lower case; the expectation is one of the rejections listed in `audit/README.md`.
3. Save it under `canaries/<problem>/<step>/` and run `ztproof canaries --only <problem>`.
4. Commit it. From then on it must die on every run.

Two differences from the book's example are worth knowing. There, the canary "proves" `FreyLevelSix` by declaring it. Here `FreyLevelSix` is never a claim to prove: it is a hypothesis of the final theorem, so declaring it gains a cheat nothing, and the canaries declare the conclusion and the Frey triple step, which would. And the Fermat-three claims have no `native_decide` canary, because `native_decide` only does harm inside an otherwise finished proof, and nobody has one. That move is covered on a golden claim.

Canaries show that the gate stops the cheats somebody thought of. They say nothing about the ones nobody did, which is why `leanchecker` and the allow-list do not depend on a list of known tricks.

## Quick start

You need Python 3.11 or later and the Lean toolchain named in `lean-toolchain`, installed with [elan](https://github.com/leanprover/elan).

```bash
pip install -e '.[dev]'
lake exe cache get        # fetches the Mathlib build; a few minutes, once

pytest -q                 # the gate against its own cheats
ztproof canaries          # every canary is rejected, each for its own reason
ztproof problems          # what is pinned, and what has been reviewed
ztproof check             # every pinned statement elaborates
ztproof golden            # the golden set's reference proofs pass the gate
ztproof recheck           # every accepted proof still passes

# The exact prompt for one claim, without asking anybody:
ztproof prompt --problem fermat-three --step main --max-attempts 8

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
| Every canary was rejected, and for its own reason | Yes, when the canaries were run with the calibration, as the workflow does |
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

**CI** runs on every push and needs no secret. It runs the gate's test suite, sends every canary through the gate, checks the golden set against its reference proofs, checks that every pinned statement elaborates, and puts every accepted proof through the gate again.

**Prove** runs when a problem, a batch, a prompt or the golden set changes, or on demand. It needs one repository secret, `ANTHROPIC_API_KEY`; without it the workflow says so and records nothing. With it, the workflow runs Stage 0 first, and Stage 0 begins with the canaries: if one of them is accepted, or dies of the wrong thing, the job stops before a single token is spent. The results are kept in the golden run as `canaries.json`. On a no-go the job fails there, the calibration is committed to `audit/runs/`, and no document of the batch is touched. On a go it starts a run for the batch it was given, with the worker pools Stage 0 computed, alternates "ask the model" and "verify in Lean" for up to eight rounds, commits both runs to `audit/runs/` and any accepted proof to `proofs/`, and prints the calibration and the referee report as the job summary.

The key and the untrusted Lean code never share a step. Steps that ask the model hold the key and run no Lean; steps that verify run Lean and hold no key. This is why the rounds are written out one by one in the workflow, which is less elegant than a loop and rather the point.

The model defaults to `claude-opus-5-5` at effort `high`. Set the repository variables `ZTPROOF_MODEL` and `ZTPROOF_EFFORT` to change either. If the model declines a request on policy grounds, the API may answer with a fallback model; the record then shows both the model asked and the model that answered.

A test reads the workflow file as data and fails if any step that holds the key does anything but ask the model, or if any step that runs Lean holds the key.

## The context pack

A context pack is one YAML file per problem, `problems/<id>/context.yaml`: what the model may be told beyond the statement. It is written before a run, versioned with the problem, and identical for every attempt. The Fermat-three pack is the author's; the four packs of the training ladder were written by the platform's build thread and wait for a person's review. It is not a context map: nothing is retrieved, ranked or selected at run time, and the whole of it fits on a page.

It holds the problem in plain words, its level and field; the toolchain; the imports; the conventions of the formalisation; the **named assumptions**, each with what it stands for and who reviewed it; what is *not* in Mathlib, so that the model does not go looking or invent names; what is forbidden; and a budget.

The pack is an input like any other, so the platform checks whatever it can.

| The pack says | Checked against | When they disagree |
| --- | --- | --- |
| `toolchain` | `lean-toolchain` and `lake-manifest.json` | The problem does not load |
| `imports` | The problem's own imports | The problem does not load |
| `assumptions` | Each must be a definition in the problem's preamble and a hypothesis of a pinned statement | The problem does not load |
| An unknown or misspelt key | The list of keys the platform understands | The problem does not load. A rule nobody reads is a rule nobody enforces |
| The file itself | Its SHA-256, recorded when a run starts and with every attempt | A pack that changes during a run stops the run |

What the pack forbids is told to the model, and then enforced as if the model had never read it.

| Forbidden | What stops it | Outcome |
| --- | --- | --- |
| `sorry`, `admit` | The axioms of the compiled proof include `sorryAx` | `sorry` |
| `axiom` | Any axiom beyond `propext`, `Classical.choice`, `Quot.sound` | `forbidden_axiom`, or `not_a_theorem` |
| `native_decide`, `decide +native` | Both leave an axiom that trusts the compiler | `forbidden_axiom` |
| Changing the definitions | The platform writes them above every attempt. A second definition under the same name does not match | `redefinition` or `statement_mismatch` |
| Changing the statement, adding a hypothesis | The attempt's statement and the pinned one are compared as kernel terms | `statement_mismatch` |
| Changing `lean-toolchain` or `lakefile.toml` | A model sends one block of Lean text and writes no file | Not possible |
| Raising `maxHeartbeats` | A check on the text, then the wall clock, which kills Lean at the limit | `budget_override`, `lean_timeout` |

A named assumption is the honest way to stand on a theorem nobody has formalised. It is a definition, it appears as a hypothesis in the statement, and the report calls the result what it is: proved *assuming* it. It is never an axiom: a problem whose preamble declares one does not load, and a proof that uses one is rejected.

The budget's `max_attempts` caps the attempts for that problem below whatever the run allows. `max_seconds_per_build` is the hard limit. `max_heartbeats` becomes Lean's default for the attempt.

## The author's layout, file by file

The author's example describes one exercise as a Lean project, `fermat3/`, with an owner for every file. This platform runs batches of documents, so the files are arranged differently, but each has a counterpart with the same owner.

| In the example | Owner | On this platform |
| --- | --- | --- |
| `lean-toolchain`, `lakefile.toml` | Human | The same files, at the root |
| `lake-manifest.json` | Lake | The same file, always committed |
| `Fermat3/Defs.lean` | Human | `preamble` in `problems/fermat-three/problem.toml`, written out as `ZtPreamble.lean` for every check |
| `Fermat3/Challenge.lean` | Human | Each `[[steps]]` entry, written out as `ZtStatement.lean` with `:= sorry` |
| `Fermat3/Solution.lean` | Model | The one `lean` block of a reply, kept as `attempt.lean`. The only text a model writes |
| `Fermat3/Audit.lean` | Human | `lean/Gate.lean`, which reads the axioms from the compiled module where `#print axioms` would print them |
| `canaries/` | Human | `canaries/<problem>/<step>/`, run by `ztproof canaries`. Also `tests/fixtures/gate/` and the false statement of `golden-controls` |
| `agents/context.yaml` | Human | `problems/<id>/context.yaml` |
| `agents/prompts/` | Human | `prompts/v2/` |
| `agents/orchestrator.py` | Human | `src/ztproof/pipeline.py` |
| `agents/attempts.jsonl` | Logger | `audit/runs/<run>/attempts.jsonl` |
| `attempts/` | Logger | `audit/runs/<run>/<problem>/<step>/attempt-NN/` |
| `ci/verify.sh`, `ci/log_attempt.py` | Human | `ztproof verify` |
| `comparator.json` | Human | No file: the comparison of statement and solution is `lean/Gate.lean` |
| `.github/workflows/proof-gates.yml` | Human | `ci.yml` and `prove.yml` |
| `tools/` | Model, then human review | Not built. The model has no tools here and writes no code but its one Lean block |

## The training ladder

The book has a chapter on learning Lean 4 by a progression of classic exercises, from A-level to PhD. The platform pins the same list, as the batch `training`: five rungs, one problem per rung, fifteen exercises. Each rung has a context pack that names its level, and the budget grows as the rungs do.

| Rung | Level | Problem | Imports | Exercises |
| --- | --- | --- | --- | --- |
| 1 | A-level | `training-1-a-level` | Nothing | (a) `1 + 2 + \cdots + n = \frac{n(n+1)}{2}`$, by induction. (b) If `n^2`$ is even, `n`$ is even. (c) `\sqrt{2}`$ is irrational. (d) There are infinitely many primes |
| 2 | First-year undergraduate | `training-2-first-year` | Nothing | (a) Bernoulli's inequality, `1 + nx \le (1 + x)^n`$. (b) Cantor's theorem. (c) Euclid's lemma. (d) The pigeonhole principle |
| 3 | Final-year undergraduate | `training-3-final-year` | Mathlib | (a) The limit of a real sequence is unique. (b) A group with `g^2 = 1`$ for every `g`$ is abelian. (c) Fermat's little theorem |
| 4 | Master's | `training-4-masters` | Mathlib | (a) `x^4 + y^4 = z^4`$ has no solution with `x`$, `y`$ non-zero. (b) A prime `p \equiv 1 \pmod{4}`$ is a sum of two squares. (c) The only integer solutions of `y^2 = x^3 - 2`$ are `(3, \pm 5)`$ |
| 5 | PhD | `fermat-three` | Mathlib | The worked exercise: `x^p + y^p = 3z^p`$, under the named assumption `FreyLevelSix` |

```bash
ztproof problems                                   # every rung, every statement, who reviewed it
ztproof prompt --problem training-1-a-level --step sqrt_two --max-attempts 4

# The reader's turn: your own proof, through the same gate as the model's.
ztproof gate --problem training-1-a-level --step triangular --file my_proof.lean

# The model's turn. Needs ANTHROPIC_API_KEY and spends real tokens:
ztproof run --batch training --jobs 4
```

Three things to know before reading a result.

* **Rungs 1 and 2 import nothing.** They are stated in Lean's core library, with their own two-line definition of a prime. There is no library lemma to cite, so the proof has to be carried out. This also makes them quick to check: seconds, not minutes.
* **Rungs 3 and 4 import Mathlib, and Mathlib already knows most of the answers.** It holds 3(a), 3(b), 3(c), 4(a) and 4(b) in some form, so a one-line citation is a valid proof and the gate accepts it. That is a lesson in using a library and no lesson in descent. The gate cannot yet be told that a named theorem is off limits. To our knowledge Mathlib does not hold 4(c).
* **Nobody has reviewed a statement.** The fourteen new statements, and the four context packs, were written by the platform's build thread on 17 November 2026. Each says so. The first exercise has been proved by hand and accepted by the gate, as a test that the rung works; no other has been proved by anybody here, and no model has been asked.

A reader's attempt through `ztproof gate` is checked exactly as a model's is, and is not recorded: the audit trail holds real model runs only.

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

A problem is one TOML file, one per document, with a context pack next to it if the model is to be told more than the statement. Each step is one link of the chain: an informal claim, and a Lean statement with a name for who formalised it and who reviewed it. A step without a Lean statement is listed and never sent to a model.

Formulas in the informal text use the manuscript's convention, a code span followed by a dollar sign, as in `n^2`$.

Facts a problem lets us assume, and earlier parts a later part relies on, enter a Lean statement as explicit hypotheses. They never enter as axioms, which the gate would reject in any case.

Eight problems are pinned today. Four are the rungs of the training ladder, described above. The others:

* `fermat-three`: the book's worked exercise. For a prime `p \ge 5`$, the equation `x^p + y^p = 3z^p`$ has no solution in non-zero integers with `\gcd(x, y, z) = 1`$. Three claims, a chain: the solution is pairwise coprime; it gives a Frey triple; and, assuming `FreyLevelSix`, there is no solution. `FreyLevelSix` stands for the modular method at level 6, none of which is in Mathlib. The Lean statements and the definition of the assumption have not been reviewed by a number theorist, and the reports say so on every page.
* `golden-core`: five small known results in core Lean, the positive half of the golden set. If one of these fails, look at the platform before blaming the model.
* `golden-controls`: one false statement, the negative half. A run that names no batch leaves both golden problems out: canaries are not results.
* `class-number`: the earlier exercise, Dirichlet's class number formula for `\mathbb{Q}(\sqrt{-p})`$, in nine steps. One step has a Lean statement, unreviewed. Nothing further is being done on it.

## The audit trail and the referee report

`audit/README.md` describes the layout and every outcome. In short: for each attempt we keep the exact prompt, the exact reply, the Lean text, Lean's messages, each gate stage with its duration, the axioms found, the versions of Lean and Mathlib, and SHA-256 fingerprints of the statement and of the checked file.

Each run also gets `REPORT.md`, written for a reader who trusts neither the model nor us. It opens with a summary by document: claims pinned, proved, attempted and not proved, without a Lean statement, and how many statements no human has reviewed. Then, for every claim, it gives the informal text and where it sits in the document, the pinned statement, who reviewed it, the evidence for acceptance check by check, the history of attempts, and a closing section on what the run did not establish. Each document has the same report on its own, in `<run>/<document>/REPORT.md`.

A run cleared by Stage 0 names the calibration in its report. A calibration run files nothing under `proofs/`.

## What we still do not trust

* **The statements.** Lean checks that a proof matches a statement. Whether the statement says what the mathematician meant is a human judgement, and today every statement here is unreviewed.
* **The named assumption.** `FreyLevelSix` is a Lean definition written to stand for five deep theorems. If it claims more than they prove, the final result is empty; if it is false, the result is vacuously true and Lean will not complain. Only a number theorist reading the definition can tell, and none has yet.
* **Elaboration.** Compiling a Lean file can run arbitrary code. We keep the key out of those steps and give Lean a stripped environment, but there is no sandbox yet, so a hostile attempt could still interfere with the files of its own run.
* **One kernel.** Acceptance rests on Lean's reference kernel. The toolchain ships independent checkers; we do not use them yet.
* **The golden set.** Five easy results and one false statement say that the plumbing works and that one particular hole is closed. They say little about hard claims, and nothing about Mathlib. A go means "not obviously broken", never "correct".
* **Ourselves.** `lean/Gate.lean` and `src/ztproof/gate.py` are the trusted code. They are small, tested against cheats, and have already been wrong once; see `CHANGELOG.md`.

## Version history

`CHANGELOG.md`, failures included.
