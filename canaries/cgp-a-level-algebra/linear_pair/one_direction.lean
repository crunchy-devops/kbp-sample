-- move: one_direction
-- expect: statement_mismatch
-- The textbook's "check your answer": the values satisfy the equations. That is only the
-- right-to-left half of "solve". It compiles, it is true, and it does not show that the
-- solution is the only one.
theorem Ladder.linear_pair (x y : ℝ) :
    (x = 2 ∧ y = 5 / 2) → (3 * x - 2 * y = 1 ∧ 2 * x + 3 * y = 23 / 2) := by
  rintro ⟨rfl, rfl⟩
  constructor <;> norm_num
