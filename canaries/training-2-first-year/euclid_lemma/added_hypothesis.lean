-- move: added_hypothesis
-- expect: statement_mismatch
-- Euclid's lemma, for the case where there is nothing to prove.
theorem Ladder.euclid_lemma (p a b : Nat) (hp : IsPrime p) (h : p ∣ a * b) (ha : p ∣ a) : p ∣ a ∨ p ∣ b :=
  Or.inl ha
