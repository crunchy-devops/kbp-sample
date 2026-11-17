-- The newer spelling of the same shortcut: `decide +native` also trusts the compiler,
-- and leaves its own axiom behind.
theorem count_check : ((List.range 200).filter (fun n => n * n % 7 = 2)).length = 57 := by
  decide +native
