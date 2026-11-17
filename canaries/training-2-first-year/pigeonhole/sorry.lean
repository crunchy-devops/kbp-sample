-- move: sorry
-- expect: sorry
-- The pigeons are left to sort themselves out.
theorem Ladder.pigeonhole (n : Nat) (f : Fin (n + 1) → Fin n) : ∃ i j : Fin (n + 1), i ≠ j ∧ f i = f j := by
  sorry
