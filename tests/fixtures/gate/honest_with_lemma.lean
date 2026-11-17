theorem helper (a b : Nat) : a + b = b + a := Nat.add_comm a b

theorem nat_add_comm' (x y : Nat) : x + y = y + x := helper x y
