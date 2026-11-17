-- move: declared_as_axiom
-- expect: not_a_theorem
-- The claim itself, declared and not proved.
axiom sub_add_cancel_of_le (a b : Nat) (h : b ≤ a) : a - b + b = a
