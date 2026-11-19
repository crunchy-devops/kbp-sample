# The audit trail

Every run of the `Prove` workflow is committed here, whatever its result. Nothing is ever edited or deleted by hand: a run that went badly is evidence, not litter.

Only real model runs are recorded. The test suite uses a scripted stand-in for the model and writes to a temporary directory, never to this one.

## Layout

```text
audit/runs/<run id>/
  run.json              platform version, commit, batch, model, prompt version, toolchain, steps
  attempts.jsonl        one line per verified attempt, in order
  REPORT.md             the referee report for the run, with a summary by document
  calibration.json      Stage 0 only: criteria, measurements, worker pools, configuration
  CALIBRATION.md        Stage 0 only: the same, to be read
  canaries.json         Stage 0 only: every canary, the rejection it had to get and the one it got
  <problem>/REPORT.md   the referee report for one document
  <problem>/<step>/
    statement.json      does the pinned statement elaborate, who formalised and reviewed it
    attempt-NN/
      prompt.md         exactly what was sent to the model
      generation.json   model asked, model that answered, tokens, seconds, stop reason
      reply.md          exactly what came back
      attempt.lean      the Lean text extracted from the reply
      verdict.json      each gate stage, the facts read from the compiled module, the decision
```

## Reading a run

A run directory is evidence, and it is read the way a referee reads a proof: from the claim down to the step that justifies it, never from the model's own account. Eight steps, in this order. Stop at the first one that fails.

1. **Which run is this?** Open `REPORT.md` and read the table under "The run": platform version, commit, batch, model asked, prompt version, Lean and Mathlib versions, and the Stage 0 line. A production run says `GO`, with the calibration's run id. "None: this run was not cleared by a calibration" means it was started by hand; its results stand, but nothing vouches that the configuration was healthy. "The run was cut short" means stop here and read why.
2. **What was asked?** "Summary by document" counts, for each document, the claims pinned, proved, attempted and not proved, and without a Lean statement, and how many statements no human has reviewed. A claim with no Lean statement was never sent to a model: it is a gap in the formalisation, not a failure of the proof.
3. **What does each claim's status mean?** Each claim opens with one line. Read it with the table below.
4. **Why was it accepted?** Under "Evidence for acceptance", five checks, each with the tool that made it and its duration. All five must say passed. Then the axioms the proof rests on: only `propext`, `Classical.choice` and `Quot.sound` are allowed, and a proof may use fewer.
5. **What was tried?** "Attempt history" gives every attempt, its outcome and the gate's reason, with links to the files. The table of outcomes below says who decided each one: the glue, `lean`, `lean/Gate.lean` or `leanchecker`.
6. **Open the files.** In `<problem>/<step>/attempt-NN/`: `verdict.json` first, for the stages and the facts read from the compiled module; then `attempt.lean`, the only text the model wrote that counts; then `prompt.md` and `reply.md`, for what the model was told and what it said. Nothing in `reply.md` is evidence: a model's claim that its proof is complete counts for nothing.
7. **Check the fingerprints.** `statement_sha256` in `verdict.json` must match `statement.json`, and `context_sha256` must match `run.json`. If they do not, the attempt was checked against something other than what was pinned.
8. **Read what the report does not establish.** Its last section is not boilerplate: a proof of the wrong statement is still a proof, and every statement here is unreviewed until `statement.json` names a reviewer.

| Status line in `REPORT.md` | What it lets you say | What it does not let you say |
| --- | --- | --- |
| **proved**, attempt k of n | The kernel accepted a proof of the pinned Lean statement, on the toolchain named, with standard axioms only | That the Lean statement is the exercise, unless a reviewer is named |
| **proved, assuming** `X` | The statement holds whenever the named assumption does | Anything about the assumption itself |
| not proved after k attempt(s) | Every attempt was refused, each for the reason given | That the claim is false. Not proved is not disproved |
| statement invalid | Our pinned statement did not elaborate, so no model was asked | Anything about the model or the claim. It is our bug |
| not attempted, or reply awaiting verification | Nothing yet | Anything |
| not proved, for a negative control | The expected result: the statement is known to be false | |
| **ACCEPTED, AND IT MUST NOT BE** | The gate has a hole. Stop everything | |

For the training ladder there is one more step. Each exercise of the batch `training-books` has a reference solution written in the reader's seat, under `solutions/<problem>/<step>.lean`, which `ztproof solutions` checks with the same gate. Put the model's `attempt.lean` beside it and compare: the statement is the same by construction, so what differs is the route, the tactics and the axioms. The reference solution is never in the run directory, because it was never sent to the model and is not a result of the run.

## Outcomes

| Outcome | Meaning | Decided by |
| --- | --- | --- |
| `accepted` | Same statement, a theorem, standard axioms only, kernel replay passed | all five gate stages |
| `generation_failed` | No reply: authentication, quota or network | the glue |
| `refusal` | The model declined to answer | the glue |
| `truncated` | The reply hit the output limit | the glue |
| `protocol_violation` | The reply did not contain exactly one `lean` code block | the glue |
| `budget_override` | The attempt sets `maxHeartbeats`, which the problem's context pack fixes | the glue, before Lean runs |
| `lean_error` | The attempt does not elaborate | `lean` |
| `lean_timeout` | Lean did not finish in the time allowed | `lean` |
| `theorem_missing` | The attempt does not declare the theorem asked for | `lean/Gate.lean` |
| `statement_mismatch` | The attempt proves a different statement | `lean/Gate.lean` |
| `not_a_theorem` | The name is declared as an axiom or a definition | `lean/Gate.lean` |
| `redefinition` | The attempt supplies its own version of something the statement uses | `lean/Gate.lean` |
| `sorry` | The proof contains `sorry`, directly or in a lemma | `lean/Gate.lean` |
| `forbidden_axiom` | The proof needs an axiom beyond `propext`, `Classical.choice`, `Quot.sound` | `lean/Gate.lean` |
| `kernel_replay_failed` | A declaration reached the compiled file without passing the kernel | `leanchecker` |
| `statement_invalid` | Our own pinned statement does not elaborate. Our bug, not the model's | `lean` |
| `infrastructure_error` | The gate itself misbehaved. Also ours | the glue |

## Context packs

When a problem has a context pack, `run.json` records its SHA-256 under `context_packs` as the run starts, and `statement.json`, `generation.json` and every line of `attempts.jsonl` carry it as `context_sha256`. `statement.json` also lists the named assumptions the statement takes as hypotheses, and who reviewed each. If the pack on disk stops matching the fingerprint in `run.json`, the run is cut short and says why.

## Calibration runs

A run of the golden batch is recorded like any other, with two differences. Its `run.json` carries the thresholds it started with, under `calibration`, so it is judged by those and not by whatever the batch file says later. And it files nothing under `proofs/`.

`verdict.json` and `attempts.jsonl` give, for each gate stage, the seconds it took and the peak memory of the Lean process that ran it (`max_rss_mb`). Stage 0 sizes the worker pools from these.

The workflow sends every file under `canaries/` through the gate before the golden run asks its first question, and writes the results to `canaries.json`: for each canary its move, the outcome expected, the outcome given, the gate's reason and the seconds it took. The calibration reads that file. A canary that was accepted, one that was rejected for another reason, or a set that lacks one of the five required moves, is a no-go. In practice the job has already stopped at the canaries step by then; the criterion is there so that the calibration cannot say go on its own.

A run that Stage 0 cleared records the calibration under `calibrated_by` in its `run.json`. A no-go is committed as well: a batch that did not start is a result.

A negative control that ends "not proved" is the expected result, and the report says so. A negative control that ends "accepted" is the most serious line this trail can contain.

A step that is not proved is not disproved. The trail says what was tried and why each try was refused, and no more.
