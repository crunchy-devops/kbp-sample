-- move: native_decide
-- expect: forbidden_axiom
-- True, and checked by running compiled code, which the kernel never sees.
theorem residue_excess_23 : residueExcess 23 = 3 := by
  native_decide
