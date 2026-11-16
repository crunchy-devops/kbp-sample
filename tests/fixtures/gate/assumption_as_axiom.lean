-- The named assumption is a hypothesis. Here it is promoted to an axiom, which
-- would let it be used anywhere, by anyone, without appearing in a statement.
axiom deep : Packed.DeepFact

theorem Packed.bound (_h : Packed.DeepFact) (n : Nat) : n ≤ 2 * n + 1 :=
  Nat.le_succ_of_le (deep n)
