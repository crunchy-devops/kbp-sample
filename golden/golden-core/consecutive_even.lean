theorem consecutive_product_even (n : Nat) : n * (n + 1) % 2 = 0 := by
  rcases Nat.mod_two_eq_zero_or_one n with h | h <;>
    simp [Nat.mul_mod, Nat.add_mod, h]
