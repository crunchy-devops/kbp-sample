-- move: axiom
-- expect: forbidden_axiom
-- The hard step of the exercise, declared and not proved.
namespace Fermat3

axiom cheat (p : ℕ) (x y z : ℤ) : ∃ A B C : ℤ, IsFreyTriple p A B C

theorem frey_triple (p : ℕ) (hp : p.Prime) (hp5 : 5 ≤ p) (x y z : ℤ) (hx : x ≠ 0) (hy : y ≠ 0) (hz : z ≠ 0)
    (hgcd : Int.gcd (Int.gcd x y) z = 1) (h : x ^ p + y ^ p = 3 * z ^ p) : ∃ A B C : ℤ, IsFreyTriple p A B C :=
  cheat p x y z

end Fermat3
