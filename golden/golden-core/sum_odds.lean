theorem sum_odds_eq_sq (n : Nat) : sumOdds n = n ^ 2 := by
  induction n with
  | zero => rfl
  | succ k ih =>
    simp only [sumOdds, ih, Nat.pow_two, Nat.add_mul, Nat.mul_add]
    omega
