# Version history

Every version lists what it does, what it does not do, and what went wrong on the way. The failures are kept on purpose: they are source material for *Zero-trust AI*.

## 0.3.0, 15 November 2026

Stage 0. No batch starts cold: a golden set goes through the same pipeline first, and its result decides whether the batch may start and how many workers it gets.

### What changed

* A **golden set**: `golden-core`, five small known results (the old `smoke` problem, renamed and extended), each with a hand-written reference proof under `golden/`; and `golden-controls`, one statement known to be false. `ztproof golden` puts the reference proofs through the gate with no model, in CI, on every push.
* **Negative controls.** A step may be marked `expect = "not_proved"`, only in a problem whose status is `control`. The prompt tells the model the statement may be false. An accepted control is a no-go whatever else was measured, and is never filed as a proof.
* **`ztproof calibrate`** reads a finished golden run and writes `CALIBRATION.md` and `calibration.json`: go or no-go against the thresholds in `batches/golden.toml`, pass rate, Pass@1, outcomes by class, tokens per request and per minute, seconds per gate stage, and a comparison with the previous calibration on file.
* **Measured memory.** Every Lean process now reports its own peak resident memory, per gate stage, in `verdict.json` and `attempts.jsonl`. A Lean process that exceeds its time limit is now killed with its children.
* **Worker pools from measurements.** Lean checks at a time: CPUs, or 80 % of memory over the peak of one Lean process, whichever is smaller. Model requests at a time: the account's tokens-per-minute limit over the measured rate of one worker, when the limit is given.
* **A go is tied to a configuration**: platform code and prompts (one fingerprint over the tracked files that decide behaviour), model asked and models that answered, effort, attempts allowed, Lean and Mathlib builds, runner image. `ztproof init --calibration RUN` refuses a no-go and a calibration made with another configuration.
* The **Prove workflow** runs Stage 0 first, in the same job and on the same machine as the batch. On a no-go the job fails, the calibration is committed, and the batch never starts.
* A run that names no batch now leaves the golden problems out.

### What it does not do yet

* Enforce Pass@1. A threshold needs a baseline, a baseline needs real runs, and there have been none. Pass@1 is measured, shown next to the previous calibration, and enforces nothing. The same goes for the memory limit.
* Measure Mathlib. The golden set is core Lean only, so Stage 0 knows nothing about the memory or time of a claim that imports Mathlib. Until a golden claim does, such a batch gets its Lean checks one at a time.
* Evaluate claim extraction, a formaliser with an adversarial auditor, or containerised runners. The platform has none of these stages, and the calibration report lists them as not evaluated, not as passed.
* Give a baseline as a range over several runs. One calibration is one sample of a model that does not answer the same way twice.
* Everything listed under 0.2.0 and 0.1.0 that is not mentioned above.

### What was measured on the way

On the build machine (4 CPUs, 16 GB), for the five reference proofs, all core Lean: the whole gate takes about 3 seconds per proof. The largest process is not the one that compiles the proof, at about 470 MB. It is the gate's own fact-reading program, `lean/Gate.lean`, at about 2 300 MB, most likely because it imports Lean's metaprogramming library to inspect a compiled module. The inspector is five times heavier than the thing inspected.

### What went wrong on the way

1. **The worker count in 0.2.0 was a guess.** The Prove workflow ran two Lean checks at a time because two seemed reasonable. Nobody had measured anything. With the figure above, two checks need about 4.6 GB before Mathlib is even loaded, which a small runner may not have. The number is now computed, and where it cannot be computed it is one.
2. **The first identity for a configuration made every go invalid.** The commit was part of it. But the workflow commits the calibration's own audit trail, which moves the commit, so the batch that followed could never have matched the calibration that cleared it. Caught while writing the workflow, before any run. The commit is now replaced by a fingerprint of the files that decide behaviour.
3. **A false statement was about to become a research problem.** In 0.2.0, a run that named no batch took every problem in `problems/`. With the negative control added, that included a statement known to be false, to be attempted on every push at the full price. Also caught before any run.
4. **Still nothing has run for real.** The push is still refused, so there has been no CI run, and there is no model key, so there has been no calibration. Version 0.3.0 can decide whether a batch may start and has never decided it once. Every figure above comes from the reference proofs, not from a model.

## 0.2.0, 15 November 2026

Batches. The platform's unit of work is now a batch of documents, so that a set of arXiv preprints can be put through the same gate in one run and each comes out with its own referee report.

### What changed

* A problem now describes its **document**: kind (preprint, exercise or note), arXiv identifier, the version that was read, authors, and the SHA-256 of the file the claims were read from. A preprint without an identifier and a version is refused, since "the latest version" is not something a referee can cite.
* Each step can carry a **locator**, the place of the claim in the document, such as "Theorem 1.2, page 3".
* **Batches** are named lists of problems in `batches/`. `ztproof init --batch NAME` starts one run for all of them, and the `Prove` workflow takes the batch as an input.
* Model requests and Lean checks run **in parallel** (`--jobs`). The audit trail is still written in the run's own step order, whichever process finishes first. The first request of a round always goes alone, so a broken key is recorded once and not once per claim.
* The run's report opens with a **summary by document**: claims pinned, proved, attempted and not proved, without a Lean statement, not examined, and how many Lean statements nobody has reviewed. Each document also gets its own `REPORT.md`.

### What it does not do yet

* Read a preprint. Claims and their Lean statements are still pinned by hand. A stage that extracts the claims of a preprint and proposes Lean statements for human review is the next piece, and it will treat the preprint's text as untrusted input like everything else.
* Split a very large batch across several CI machines. One run uses one runner.
* Everything listed under 0.1.0, which is all still true.

### What went wrong on the way

1. **Version 0.1.0 never reached GitHub.** The push was refused with HTTP 403: the build session had lost write access to the repository. The commit, its tests and its claims therefore stayed on one machine overnight, checked by nobody else, which is a fair description of the problem this book is about. No CI run has happened yet, and no model has been asked anything.

## 0.1.0, 14 November 2026

The first version: a conjecture step goes from a prompt to a Lean 4 verdict, under Python glue, in GitHub Actions, with every attempt recorded. No context map.

### What it does

* Pins problems as `problems/<id>/problem.toml`: informal text, one step per link of the chain, a Lean statement per step with the name of who formalised it and who reviewed it.
* Builds one self-contained prompt per attempt from versioned templates in `prompts/v1/`. From the second attempt on, the prompt carries the previous Lean text, the verdict and Lean's messages.
* Asks one model (Claude through the Anthropic API) once per open step per round.
* Checks each reply with a five-stage gate: our statement elaborates, the attempt elaborates, `lean/Gate.lean` reads both compiled modules as data, a policy accepts only the same statement proved as a theorem on the three standard axioms, and `leanchecker` replays the attempt through the kernel.
* Records every attempt under `audit/runs/`, files accepted proofs under `proofs/`, and writes a referee report per run.
* Rechecks every accepted proof on every push.

### What it does not do yet

* Run a single real model attempt. The repository has no model key at the time of this release, so the audit trail is empty. That is the correct content for it.
* Formalise the worked exercise. Of its nine steps, one has a Lean statement, and that statement has not been reviewed.
* Sandbox Lean. Elaborating an attempt can run arbitrary code on the CI runner. The key is kept out of those steps and out of Lean's environment, which limits the damage and does not remove the problem.
* Decompose a step into lemmas, or assemble proved steps into one theorem.

### What went wrong on the way

These happened during the build on 14 November 2026. None involved a model; the platform's authors are quite capable of failing unaided.

1. **The honest proof was wrong.** The first "honest" fixture, a proof that `1 + 3 + \cdots + (2n-1) = n^2`$, closed its induction step with `omega`. Lean refused: after rewriting, the goal still contained the product `k \cdot k`$, and `omega` only decides linear arithmetic. The gate reported `lean_error` on the one file meant to pass. Fixed by distributing the products first.
2. **The gate accepted an `import`.** The first attempt header was a single line, `import ZtPreamble`. An attempt beginning with `import Lean` was accepted, because consecutive imports at the top of a file all belong to its header. So the "fixed" imports were not fixed. The proof in question was honest and the other stages would still have applied, but the model could choose its own imports, and with them Lean's metaprogramming library. Found by the `import_line` fixture, which expected a rejection and got an acceptance. Fixed by ending the header with a module comment, which is a command: Lean itself now rejects any later `import`.
3. **A cheat fixture did not compile.** The first `shadowed_definition` fixture opened a namespace next to the real definition and died of an ambiguity error, so it tested Lean's name resolution and not the gate. Rewritten to compile cleanly, it is now rejected for the right reason, `statement_mismatch`.
4. **No Mathlib in the build sandbox.** The machine the platform was built on can fetch Mathlib's sources but not its prebuilt cache, and building Mathlib from source takes hours. Every core-Lean check was run locally. The one Mathlib statement has therefore not been elaborated anywhere yet: it waits for the first CI run, and may well fail it.
