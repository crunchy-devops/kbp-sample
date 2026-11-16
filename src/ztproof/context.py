"""The context pack: everything a model may be told about one problem, in one file.

`problems/<id>/context.yaml` is written and reviewed by people, versioned with the
problem, and never changed during a run. It is not a context map and there is no
retrieval: the pack is a fixed text, small enough to read, and the same for every
attempt. Its fingerprint goes into every record.

A pack is not trusted for its own sake. Whatever it says can be checked against
something else is checked here or in the gate:

  toolchain     against `lean-toolchain` and `lake-manifest.json`
  imports       against the problem's own imports
  assumptions   must be definitions in the problem's preamble, and hypotheses of a
                pinned statement. The gate rejects an axiom in any case.
  forbidden     told to the model; enforced by the gate, whatever the model read
  budget        attempts and seconds are enforced; see `Budget`
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

_LEAN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*(\.[A-Za-z_][A-Za-z0-9_']*)*$")
_TOP = {"problem", "library", "toolchain", "imports", "conventions", "assumptions", "not_in_mathlib",
        "forbidden", "budget"}
_PROBLEM = {"id", "informal", "level", "field", "source"}
_ASSUMPTION = {"name", "file", "stands_for", "how_to_use", "reviewed_by"}
_BUDGET = {"max_attempts", "max_seconds_per_build", "max_heartbeats"}


class ContextError(ValueError):
    """A context pack is malformed or contradicts the platform's own pins."""


@dataclass(frozen=True)
class Assumption:
    """A statement the problem lets us assume. It enters as a hypothesis, never as an axiom."""
    name: str
    stands_for: str
    how_to_use: str
    reviewed_by: str         # empty until a named person has reviewed the Lean definition

    @property
    def short(self) -> str:
        return self.name.split(".")[-1]


@dataclass(frozen=True)
class Budget:
    """Limits for one problem.

    `max_attempts` caps the attempts of a run. `max_seconds_per_build` caps the wall
    clock of one Lean compilation, and the process is killed at the limit: this is
    the hard one. `max_heartbeats` is given to Lean as the default of its
    `maxHeartbeats` option, and an attempt that sets the option itself is refused
    before Lean runs. That check reads text, so it stops the honest and the lazy;
    the wall clock stops the rest.
    """
    max_attempts: int | None = None
    max_seconds_per_build: float | None = None
    max_heartbeats: int | None = None


@dataclass(frozen=True)
class ContextPack:
    problem_id: str
    informal: str
    level: str
    field: str
    source: str
    library: str
    lean: str
    mathlib: str
    imports: tuple[str, ...]
    conventions: tuple[str, ...]
    assumptions: tuple[Assumption, ...]
    not_in_mathlib: tuple[str, ...]
    forbidden: tuple[str, ...]
    budget: Budget
    sha256: str              # of the file's bytes, exactly as committed


def _strings(data: dict, key: str, where: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ContextError(f"{where}: {key} must be a list of non-empty strings")
    return tuple(v.strip() for v in value)


def _table(data: dict, key: str, where: str, allowed: set[str]) -> dict:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ContextError(f"{where}: {key} must be a table")
    unknown = sorted(set(value) - allowed)
    if unknown:
        # A misspelt key that is silently ignored is a rule nobody enforces.
        raise ContextError(f"{where}: unknown key(s) under {key}: {', '.join(unknown)}")
    return value


def _str(table: dict, key: str, where: str, *, required: bool = False) -> str:
    value = table.get(key, "")
    if not isinstance(value, str):
        raise ContextError(f"{where}: {key} must be a string")
    if required and not value.strip():
        raise ContextError(f"{where}: {key} is required")
    return " ".join(value.split())


def _reviewer(text: str) -> str:
    """A placeholder such as "<name of the number theorist>, <date>" is not a review."""
    return "" if "<" in text or not text.strip() else text.strip()


def _limit(table: dict, key: str, where: str, kind: type, low: float, high: float):
    value = table.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ContextError(f"{where}: budget.{key} must be a number between {low} and {high}")
    if kind is int and int(value) != value:
        raise ContextError(f"{where}: budget.{key} must be a whole number")
    return kind(value)


def load_pack(path: Path) -> ContextPack:
    where = str(path)
    raw = path.read_bytes()
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ContextError(f"{where}: {exc}") from exc
    if not isinstance(data, dict):
        raise ContextError(f"{where}: a context pack is a table of keys")
    unknown = sorted(set(data) - _TOP)
    if unknown:
        raise ContextError(f"{where}: unknown key(s): {', '.join(unknown)}")

    problem = _table(data, "problem", where, _PROBLEM)
    toolchain = _table(data, "toolchain", where, {"lean", "mathlib"})
    budget = _table(data, "budget", where, _BUDGET)
    assumptions: list[Assumption] = []
    raw_assumptions = data.get("assumptions", [])
    if not isinstance(raw_assumptions, list):
        raise ContextError(f"{where}: assumptions must be a list")
    for index, item in enumerate(raw_assumptions):
        awhere = f"{where}: assumptions[{index}]"
        if not isinstance(item, dict) or set(item) - _ASSUMPTION:
            raise ContextError(f"{awhere}: expected the keys {', '.join(sorted(_ASSUMPTION))}")
        name = _str(item, "name", awhere, required=True)
        if not _LEAN_NAME.match(name):
            raise ContextError(f"{awhere}: {name!r} is not a Lean name")
        assumptions.append(Assumption(
            name=name, stands_for=_str(item, "stands_for", awhere, required=True),
            how_to_use=_str(item, "how_to_use", awhere, required=True),
            reviewed_by=_reviewer(_str(item, "reviewed_by", awhere)),
        ))
    if len({a.name for a in assumptions}) != len(assumptions):
        raise ContextError(f"{where}: an assumption is listed twice")
    library = data.get("library", "")
    if not isinstance(library, str) or (library and not _LEAN_NAME.match(library)):
        raise ContextError(f"{where}: library must be a Lean name")

    return ContextPack(
        problem_id=_str(problem, "id", where, required=True),
        informal=_str(problem, "informal", where),
        level=_str(problem, "level", where),
        field=_str(problem, "field", where),
        source=_str(problem, "source", where),
        library=library,
        lean=_str(toolchain, "lean", where, required=True),
        mathlib=_str(toolchain, "mathlib", where),
        imports=_strings(data, "imports", where),
        conventions=_strings(data, "conventions", where),
        assumptions=tuple(assumptions),
        not_in_mathlib=_strings(data, "not_in_mathlib", where),
        forbidden=_strings(data, "forbidden", where),
        budget=Budget(
            max_attempts=_limit(budget, "max_attempts", where, int, 1, 20),
            max_seconds_per_build=_limit(budget, "max_seconds_per_build", where, float, 1, 86_400),
            max_heartbeats=_limit(budget, "max_heartbeats", where, int, 1, 10**9),
        ),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def pinned_toolchain(root: Path = REPO_ROOT) -> tuple[str, str]:
    """What the repository really pins: (Lean toolchain, Mathlib revision asked for)."""
    lean = (root / "lean-toolchain").read_text(encoding="utf-8").strip()
    mathlib = ""
    manifest = root / "lake-manifest.json"
    if manifest.is_file():
        for package in json.loads(manifest.read_text(encoding="utf-8"))["packages"]:
            if package["name"] == "mathlib":
                mathlib = package.get("inputRev") or package["rev"]
    return lean, mathlib


_AXIOM = re.compile(r"^\s*(?:(?:private|protected|noncomputable|unsafe)\s+)*axiom\b", re.MULTILINE)
SET_HEARTBEATS = re.compile(r"\bset_option\s+(?:[\w.]*\.)?maxHeartbeats\b")


def preamble_declares_axiom(preamble: str) -> bool:
    return bool(_AXIOM.search(preamble))


def check_against_problem(pack: ContextPack, *, problem_id: str, imports: tuple[str, ...], preamble: str,
                          signatures: list[str], where: str, root: Path = REPO_ROOT) -> None:
    """Every statement of the pack that the platform can check for itself."""
    if pack.problem_id != problem_id:
        raise ContextError(f"{where}: the pack is for problem {pack.problem_id!r}, and sits in {problem_id!r}")
    if set(pack.imports) != set(imports):
        raise ContextError(f"{where}: the pack lists the imports {sorted(pack.imports)}, "
                           f"the problem imports {sorted(imports)}")
    lean, mathlib = pinned_toolchain(root)
    if pack.lean != lean:
        raise ContextError(f"{where}: the pack says Lean is {pack.lean!r}, lean-toolchain says {lean!r}")
    uses_mathlib = any(m.split(".")[0] == "Mathlib" for m in imports)
    if uses_mathlib and pack.mathlib != mathlib:
        raise ContextError(f"{where}: the pack says Mathlib is {pack.mathlib!r}, "
                           f"lake-manifest.json says {mathlib!r}")
    for a in pack.assumptions:
        if not re.search(rf"^\s*(?:def|abbrev|structure|class)\s+{re.escape(a.short)}\b", preamble, re.MULTILINE):
            raise ContextError(f"{where}: assumption {a.name} is not defined in the problem's preamble")
        if not any(re.search(rf"\b{re.escape(a.short)}\b", s) for s in signatures):
            raise ContextError(f"{where}: assumption {a.name} is a hypothesis of no pinned statement")
