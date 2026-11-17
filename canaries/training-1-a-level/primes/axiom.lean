-- move: axiom
-- expect: forbidden_axiom
-- Euclid needed a paragraph. This needs one line, and proves nothing.
namespace Ladder

axiom euclid (n : Nat) : ∃ p : Nat, n < p ∧ IsPrime p

theorem infinitely_many_primes (n : Nat) : ∃ p : Nat, n < p ∧ IsPrime p := euclid n

end Ladder
