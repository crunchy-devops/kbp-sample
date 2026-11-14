open Lean Elab Command Term in
set_option debug.skipKernelTC true in
run_cmd do
  let type ← liftTermElabM do
    let e ← elabType (← `((1 : Nat) = 2))
    instantiateMVars e
  liftCoreM <| addDecl <| .thmDecl {
    name := `false_claim, levelParams := [], type, value := mkConst ``True.intro }
