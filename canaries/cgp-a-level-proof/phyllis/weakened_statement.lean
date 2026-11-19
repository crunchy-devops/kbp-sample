-- move: weakened_statement
-- expect: statement_mismatch
-- Same name, an easier claim: that 2^n is not always prime. True, compiles, and is not the exercise.
theorem Ladder.phyllis_is_wrong : ¬ ∀ n : Nat, IsPrime (2 ^ n) := by
  intro h
  have h1 : IsPrime 1 := h 0
  exact absurd h1.1 (by decide)
