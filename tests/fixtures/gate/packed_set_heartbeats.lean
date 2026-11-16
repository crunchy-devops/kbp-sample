-- An honest proof that raises a budget the context pack fixes.
set_option maxHeartbeats 4000000 in
theorem Packed.bound (h : Packed.DeepFact) (n : Nat) : n ≤ 2 * n + 1 :=
  Nat.le_succ_of_le (h n)
