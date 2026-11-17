-- move: sorry
-- expect: sorry
-- The exercise, left as an exercise.
theorem Fermat3.fermat_three (hFrey : FreyLevelSix) (p : ℕ) (hp : p.Prime) (hp5 : 5 ≤ p) (x y z : ℤ) (hx : x ≠ 0) (hy : y ≠ 0) (hz : z ≠ 0) (hgcd : Int.gcd (Int.gcd x y) z = 1) :
    x ^ p + y ^ p ≠ 3 * z ^ p := by
  sorry
