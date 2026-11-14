theorem sum_odds (n : Nat) : sumOdds n = n * n := by
  induction n with
  | zero => rfl
  | succ k ih =>
    simp only [sumOdds, ih, Nat.add_mul, Nat.mul_add]
    omega
