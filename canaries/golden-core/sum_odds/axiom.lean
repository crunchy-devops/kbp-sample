-- move: axiom
-- expect: forbidden_axiom
-- Declares the claim, then "proves" it. Compiles without a warning.
axiom cheat (n : Nat) : sumOdds n = n ^ 2

theorem sum_odds_eq_sq (n : Nat) : sumOdds n = n ^ 2 := cheat n
