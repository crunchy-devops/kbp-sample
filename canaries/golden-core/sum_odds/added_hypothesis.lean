-- move: added_hypothesis
-- expect: statement_mismatch
-- Assumes the conclusion and hands it back.
theorem sum_odds_eq_sq (n : Nat) (h : sumOdds n = n ^ 2) : sumOdds n = n ^ 2 := h
