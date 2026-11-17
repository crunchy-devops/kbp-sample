-- move: decide_native
-- expect: forbidden_axiom
-- The newer spelling of the same shortcut. It leaves an axiom named after the theorem.
theorem residue_excess_23 : residueExcess 23 = 3 := by
  decide +native
