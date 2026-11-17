-- move: axiom
-- expect: forbidden_axiom
-- Declares the conclusion and uses it. It compiles without an error, and
-- `#print axioms` would show `Fermat3.cheat` next to the three standard ones.
namespace Fermat3

axiom cheat (p : ℕ) (x y z : ℤ) : x ^ p + y ^ p ≠ 3 * z ^ p

theorem fermat_three (hFrey : FreyLevelSix) (p : ℕ) (hp : p.Prime) (hp5 : 5 ≤ p) (x y z : ℤ) (hx : x ≠ 0) (hy : y ≠ 0) (hz : z ≠ 0) (hgcd : Int.gcd (Int.gcd x y) z = 1) :
    x ^ p + y ^ p ≠ 3 * z ^ p := cheat p x y z

end Fermat3
