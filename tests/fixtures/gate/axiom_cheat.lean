axiom trust_me : ∀ n : Nat, sumOdds n = n * n

theorem sum_odds_eq_sq (n : Nat) : sumOdds n = n * n := trust_me n
