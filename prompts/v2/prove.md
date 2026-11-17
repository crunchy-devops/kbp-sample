# Task

Prove one theorem in Lean 4. This is attempt {{attempt}} of {{max_attempts}}.

# The problem this step belongs to

{{problem_title}} ({{problem_status}})

{{problem_informal}}

# The step to prove{{step_part}}

{{step_informal}}

{{context}}# The Lean file

Toolchain: {{lean_version}}.
Libraries: {{libraries}}.

Your code is placed in a file that already begins with the lines below. You cannot change them.

```lean
{{preamble}}
```

The theorem to prove, with its statement exactly as pinned:

```lean
theorem {{theorem}} {{signature}} := sorry
```

# Rules

1. Reply with exactly one fenced code block labelled `lean`. It contains everything that goes below the fixed lines: helper definitions and lemmas if you want them, then the theorem.
2. Write no `import` line. The imports are fixed.
3. Declare `theorem {{theorem}}` with the pinned statement. The checker compares your statement with the pinned one as kernel terms, so a weaker, stronger or merely similar statement is rejected.
4. No `sorry`, no `admit`, no new `axiom`, no `native_decide`. The proof may depend only on `propext`, `Classical.choice` and `Quot.sound`.
5. If you cannot finish the proof, still send your best complete attempt. Lean's errors are returned to you on the next attempt.
