-- written_by: Claude, in the author's Cowork session, 19 November 2026
-- reviewed_by:
-- A reference solution, written in the reader's seat. Checked by `ztproof solutions`;
-- never sent to a model, never recorded as a model's result.
-- Book's answer (p. 69): (n + 1)^2 - (n - 1)^2 = 4n = 2(2n).
theorem Ladder.squares_difference_even (n : Int) : ∃ k, (n + 1) ^ 2 - (n - 1) ^ 2 = 2 * k := by
  refine ⟨2 * n, ?_⟩
  simp only [Int.pow_succ, Int.pow_zero, Int.one_mul, Int.mul_add, Int.add_mul, Int.sub_mul,
    Int.mul_sub]
  omega
