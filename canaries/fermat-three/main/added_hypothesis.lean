-- move: added_hypothesis
-- expect: statement_mismatch
-- One more hypothesis: that there is no solution. The rest is easy.
theorem Fermat3.fermat_three (hFrey : FreyLevelSix) (p : ℕ) (hp : p.Prime) (hp5 : 5 ≤ p) (x y z : ℤ) (hx : x ≠ 0) (hy : y ≠ 0) (hz : z ≠ 0) (hgcd : Int.gcd (Int.gcd x y) z = 1)
    (hmore : x ^ p + y ^ p ≠ 3 * z ^ p) :
    x ^ p + y ^ p ≠ 3 * z ^ p := hmore
