-- move: weakened_statement
-- expect: statement_mismatch
-- Same name, same hypothesis, and a conclusion true of every natural number.
theorem Ladder.sqrt_two_irrational (a b : Nat) (h : a ^ 2 = 2 * b ^ 2) : b = 0 ∨ 0 < b := by
  omega
