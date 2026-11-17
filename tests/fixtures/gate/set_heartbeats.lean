-- An honest proof that raises its own budget. Accepted where no budget is set,
-- refused where a context pack fixes `maxHeartbeats`.
set_option maxHeartbeats 4000000 in
theorem nat_add_comm' (a b : Nat) : a + b = b + a := Nat.add_comm a b
