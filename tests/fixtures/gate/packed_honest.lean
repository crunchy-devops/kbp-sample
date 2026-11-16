theorem Packed.bound (h : Packed.DeepFact) (n : Nat) : n ≤ 2 * n + 1 :=
  Nat.le_succ_of_le (h n)
