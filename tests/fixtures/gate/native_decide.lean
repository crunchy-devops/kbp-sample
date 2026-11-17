theorem count_check : ((List.range 200).filter (fun n => n * n % 7 = 2)).length = 57 := by
  native_decide
