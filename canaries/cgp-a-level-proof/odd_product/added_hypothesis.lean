-- move: added_hypothesis
-- expect: statement_mismatch
-- One more hypothesis: that the product is already odd. Then there is nothing left to prove.
theorem Ladder.odd_mul_odd (a b : Int) (ha : ∃ x, a = 2 * x + 1) (hb : ∃ y, b = 2 * y + 1)
    (hab : ∃ z, a * b = 2 * z + 1) : ∃ z, a * b = 2 * z + 1 := hab
