-- written_by: Claude, in the author's Cowork session, 19 November 2026
-- reviewed_by:
-- A reference solution, written in the reader's seat. Checked by `ztproof solutions`;
-- never sent to a model, never recorded as a model's result.
-- Book's answer (p. 69): n = 3 gives 2^3 + 1 = 9 = 3 × 3, which is not prime.
theorem Ladder.phyllis_is_wrong : ¬ ∀ n : Nat, IsPrime (2 ^ n + 1) := by
  intro h
  have h9 : IsPrime 9 := h 3
  rcases h9.2 3 ⟨3, rfl⟩ with h1 | h1 <;> omega
