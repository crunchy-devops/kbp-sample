-- written_by: Claude, in the author's Cowork session, 19 November 2026
-- reviewed_by:
-- A reference solution, written in the reader's seat. Checked by `ztproof solutions`;
-- never sent to a model, never recorded as a model's result.
-- Book's argument (p. 30): the expression expands to 5a^2 + 15a + 10 = 5(a^2 + 3a + 2).
theorem Ladder.multiple_of_five (a : Int) :
    ∃ x, (3 * a - 1) * (a + 2) + 2 * (a + 3) * (a + 2) = 5 * x := by
  refine ⟨a ^ 2 + 3 * a + 2, ?_⟩
  simp only [Int.pow_succ, Int.pow_zero, Int.one_mul, Int.mul_add, Int.add_mul, Int.sub_mul]
  -- omega treats a * a, 3 * a * a and 2 * a * a as unrelated unknowns: say how they relate.
  have h3 : 3 * a * a = 3 * (a * a) := Int.mul_assoc 3 a a
  have h2 : 2 * a * a = 2 * (a * a) := Int.mul_assoc 2 a a
  omega
