/-
The gate: the only component of the platform whose verdict counts.

It never elaborates the attempt. It loads two compiled modules as plain data:

* `Statement`, written by the platform from the pinned conjecture, and
* `Attempt`, written by an untrusted model,

and reports facts about them as one JSON object on standard output. The policy
(which facts make an attempt acceptable) is applied twice: here, in the `ok`
field, and again by the Python glue, which does not take `ok` on trust either.

Usage: lean --run lean/Gate.lean <StatementModule> <AttemptModule> <theoremName>
The two `.olean` files must be on `LEAN_PATH`.
-/
import Lean

open Lean

namespace ZeroTrust

/-- The three axioms of Lean's standard logic. Anything else is a rejection. -/
def allowedAxioms : List Name := [``propext, ``Classical.choice, ``Quot.sound]

/-- Constants referred to directly by a declaration: its type and, when asked, its value. -/
def directDeps (ci : ConstantInfo) (withValue : Bool) : NameSet :=
  let s := ci.type.getUsedConstantsAsSet
  let s := if withValue then
      match ci.value? (allowOpaque := true) with
      | some v => v.getUsedConstantsAsSet.foldl (init := s) fun acc n => acc.insert n
      | none => s
    else s
  match ci with
  | .inductInfo v => v.ctors.foldl (init := s) fun acc n => acc.insert n
  | _ => s

/-- Transitive closure of the constants reachable from `roots` in `env`. -/
partial def closure (env : Environment) (roots : Array Name) : NameSet := Id.run do
  let mut seen : NameSet := {}
  let mut todo := roots
  while h : todo.size > 0 do
    let n := todo[todo.size - 1]
    todo := todo.pop
    if seen.contains n then continue
    seen := seen.insert n
    if let some ci := env.constants.find? n then
      for d in directDeps ci (withValue := true) do
        unless seen.contains d do todo := todo.push d
  return seen

def kindOf : ConstantInfo → String
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "def"
  | .thmInfo _ => "theorem"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _ => "quot"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"

def names (ns : Array Name) : Json :=
  Json.arr <| (ns.map toString).qsort (· < ·) |>.map Json.str

unsafe def run (stmtMod attMod thm : Name) : IO UInt32 := do
  initSearchPath (← findSysroot)
  -- `loadExts := false`: nothing the attempt registered (syntax, macros, attributes,
  -- initialisers) is executed here. The modules are read as data.
  let envS ← importModules #[{module := stmtMod}] {} (loadExts := false)
  let envA ← importModules #[{module := attMod}] {} (loadExts := false)
  let stmt? := envS.constants.find? thm
  let att? := envA.constants.find? thm
  let mut facts : Array (String × Json) := #[
    ("theorem", toJson (toString thm)),
    ("statement_found", toJson stmt?.isSome),
    ("attempt_found", toJson att?.isSome)]
  let (some stmt, some att) := (stmt?, att?)
    | IO.println (Json.mkObj (facts.push ("ok", toJson false)).toList).compress
      return 1
  -- 1. The attempt proves the pinned statement, compared as kernel terms.
  let typeMatch := stmt.type == att.type && stmt.levelParams == att.levelParams
  -- 2. It is a theorem, not an axiom or anything else wearing its name.
  let kind := kindOf att
  -- 3. Every constant the statement talks about comes from the shared imports, so the
  --    attempt has not supplied its own meaning for any of them.
  let attIdx? := envA.header.moduleNames.idxOf? attMod
  let stmtDeps := closure envS stmt.type.getUsedConstantsAsSet.toArray
  let mut redefined : Array Name := #[]
  let mut missing : Array Name := #[]
  for c in stmtDeps do
    match envA.getModuleIdxFor? c with
    | none => missing := missing.push c
    | some idx =>
      if some idx.toNat == attIdx? then redefined := redefined.push c
      else if envS.header.moduleNames[(envS.getModuleIdxFor? c).get!.toNat]!
          != envA.header.moduleNames[idx.toNat]! then
        redefined := redefined.push c
  -- 4. The axioms the proof rests on.
  let deps := closure envA #[thm]
  let mut axioms : Array Name := #[]
  for c in deps do
    if let some (.axiomInfo _) := envA.constants.find? c then axioms := axioms.push c
  let forbidden := axioms.filter (!allowedAxioms.contains ·)
  let ok := typeMatch && kind == "theorem" && redefined.isEmpty && missing.isEmpty
    && forbidden.isEmpty && attIdx?.isSome
  facts := facts ++ #[
    ("type_match", toJson typeMatch),
    ("kind", toJson kind),
    ("redefined", names redefined),
    ("missing", names missing),
    ("axioms", names axioms),
    ("forbidden_axioms", names forbidden),
    ("ok", toJson ok)]
  IO.println (Json.mkObj facts.toList).compress
  return if ok then 0 else 1

end ZeroTrust

unsafe def main (args : List String) : IO UInt32 := do
  match args with
  | [s, a, t] => ZeroTrust.run s.toName a.toName t.toName
  | _ =>
    IO.eprintln "usage: lean --run Gate.lean <StatementModule> <AttemptModule> <theoremName>"
    return 2
