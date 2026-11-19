-- written_by: Claude, in the author's Cowork session, 19 November 2026
-- reviewed_by:
-- A reference solution, written in the reader's seat. Checked by `ztproof solutions`;
-- never sent to a model, never recorded as a model's result.
-- Book's argument (p. 30): 2x - 2y = 2(x - y), and x - y is an integer.
theorem Ladder.even_sub_even (a b : Int) (ha : ∃ x, a = 2 * x) (hb : ∃ y, b = 2 * y) :
    ∃ n, a - b = 2 * n := by
  obtain ⟨x, rfl⟩ := ha
  obtain ⟨y, rfl⟩ := hb
  exact ⟨x - y, by omega⟩
