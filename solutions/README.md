# Reference solutions

The reader's seat. Each file here is a solution to one pinned claim, written by hand for the training ladder, so that a reader can compare it with what a model sent. It is checked by the same gate as a model's attempt:

```bash
ztproof solutions                          # every reference solution, no model, no key
ztproof solutions --only cgp-a-level-proof
```

A file is `solutions/<problem>/<step>.lean`, holding exactly the Lean text a reader would send to `ztproof gate`. It begins with two comments:

```lean
-- written_by: a name and a date
-- reviewed_by:
```

`reviewed_by` stays empty until a person has read the solution. A file that names no pinned claim, or leaves out either line, stops `ztproof solutions` with an error rather than being skipped.

What a reference solution is not:

* **Not a result.** It is never filed under `proofs/` and never appears in `audit/runs/`. The audit trail holds real model runs only.
* **Not a hint.** It is never put in a prompt, and the context packs do not mention it.
* **Not a review of the statement.** It proves the pinned Lean statement. Whether that statement is the exercise is the reviewer's question, recorded in `problems/<id>/problem.toml`.

Today the seven solutions of `cgp-a-level-proof` exist. They import nothing and all pass the gate; none needs `Classical.choice`.
