-- written_by: Claude, in the author's Cowork session, 19 November 2026
-- reviewed_by:
-- A reference solution, written in the reader's seat. Checked by `ztproof solutions`;
-- never sent to a model, never recorded as a model's result.
-- Book's argument (p. 31): x = 7 is prime and 2 × 7 + 1 = 15 = 3 × 5 is not.
theorem Ladder.not_prime_double_plus_one : ¬ ∀ x : Nat, IsPrime x → IsPrime (2 * x + 1) := by
  intro h
  have h7 : IsPrime 7 := by
    refine ⟨by decide, fun d hd => ?_⟩
    have hle : d ≤ 7 := Nat.le_of_dvd (by decide) hd
    have hpos : 0 < d := Nat.pos_of_dvd_of_pos hd (by decide)
    have : d = 1 ∨ d = 2 ∨ d = 3 ∨ d = 4 ∨ d = 5 ∨ d = 6 ∨ d = 7 := by omega
    rcases this with rfl | rfl | rfl | rfl | rfl | rfl | rfl <;>
      first | exact Or.inl rfl | exact Or.inr rfl | (exfalso; revert hd; decide)
  have h15 : IsPrime 15 := h 7 h7
  rcases h15.2 3 ⟨5, rfl⟩ with h1 | h1 <;> omega
