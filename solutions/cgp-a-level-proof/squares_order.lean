-- written_by: Claude, in the author's Cowork session, 19 November 2026
-- reviewed_by:
-- A reference solution, written in the reader's seat. Checked by `ztproof solutions`;
-- never sent to a model, never recorded as a model's result.
-- Book's argument (p. 31): a = 1 > b = -2, but 1^2 + 1 = 2 < 5 = (-2)^2 + 1.
theorem Ladder.squares_not_monotone : ¬ ∀ a b : Int, b < a → b ^ 2 + 1 < a ^ 2 + 1 := by
  intro h
  have := h 1 (-2) (by decide)
  revert this
  decide
