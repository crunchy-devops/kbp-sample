-- written_by: Claude, in the author's Cowork session, 19 November 2026
-- reviewed_by:
-- A reference solution, written in the reader's seat. Checked by `ztproof solutions`;
-- never sent to a model, never recorded as a model's result.
-- Book's answer (p. 69): (2a + 1)(2b + 1) = 2(2ab + a + b) + 1.
theorem Ladder.odd_mul_odd (a b : Int) (ha : ∃ x, a = 2 * x + 1) (hb : ∃ y, b = 2 * y + 1) :
    ∃ z, a * b = 2 * z + 1 := by
  obtain ⟨x, rfl⟩ := ha
  obtain ⟨y, rfl⟩ := hb
  refine ⟨2 * x * y + x + y, ?_⟩
  simp only [Int.mul_add, Int.add_mul, Int.mul_one, Int.one_mul]
  have : 2 * x * (2 * y) = 2 * (2 * x * y) := by
    rw [Int.mul_comm 2 y, ← Int.mul_assoc, Int.mul_comm (2 * x * y) 2]
  omega
