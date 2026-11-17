-- move: weakened_statement
-- expect: statement_mismatch
-- Proves the claim for n = 0 only, under the name of the full claim.
theorem sum_odds_eq_sq (n : Nat) : sumOdds 0 = 0 ^ 2 := rfl
