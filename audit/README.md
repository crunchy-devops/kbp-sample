# The audit trail

Every run of the `Prove` workflow is committed here, whatever its result. Nothing is ever edited or deleted by hand: a run that went badly is evidence, not litter.

Only real model runs are recorded. The test suite uses a scripted stand-in for the model and writes to a temporary directory, never to this one.

## Layout

```text
audit/runs/<run id>/
  run.json              platform version, commit, model, prompt version, toolchain, steps
  attempts.jsonl        one line per verified attempt, in order
  REPORT.md             the referee report, generated from the files below
  <problem>/<step>/
    statement.json      does the pinned statement elaborate, who formalised and reviewed it
    attempt-NN/
      prompt.md         exactly what was sent to the model
      generation.json   model asked, model that answered, tokens, seconds, stop reason
      reply.md          exactly what came back
      attempt.lean      the Lean text extracted from the reply
      verdict.json      each gate stage, the facts read from the compiled module, the decision
```

## Outcomes

| Outcome | Meaning | Decided by |
| --- | --- | --- |
| `accepted` | Same statement, a theorem, standard axioms only, kernel replay passed | all five gate stages |
| `generation_failed` | No reply: authentication, quota or network | the glue |
| `refusal` | The model declined to answer | the glue |
| `truncated` | The reply hit the output limit | the glue |
| `protocol_violation` | The reply did not contain exactly one `lean` code block | the glue |
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

A step that is not proved is not disproved. The trail says what was tried and why each try was refused, and no more.
