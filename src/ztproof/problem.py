"""Problems and their steps: the pinned, human-owned side of the platform.

A problem is a directory `problems/<id>/` holding one `problem.toml`. Nothing in it
is ever written by a model. The Lean statement of each step is the one thing the
kernel cannot check for us (does it say what the mathematician meant?), so each
step records who formalised it and who reviewed it.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CORE_MODULES = {"Init", "Std", "Lean"}
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_LEAN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*(\.[A-Za-z_][A-Za-z0-9_']*)*$")


class ProblemError(ValueError):
    """A problem file is malformed. This is our bug, never the model's."""


@dataclass(frozen=True)
class Step:
    id: str
    part: str
    informal: str
    theorem: str | None
    signature: str | None
    formalised_by: str
    reviewed_by: str

    @property
    def formalised(self) -> bool:
        return bool(self.theorem and self.signature)


@dataclass(frozen=True)
class Problem:
    id: str
    title: str
    source: str
    status: str
    informal: str
    imports: tuple[str, ...]
    preamble: str
    steps: tuple[Step, ...] = field(default_factory=tuple)

    @property
    def needs_packages(self) -> bool:
        """True when a statement imports anything beyond Lean's own libraries."""
        return any(m.split(".")[0] not in CORE_MODULES for m in self.imports)

    def step(self, step_id: str) -> Step:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(f"{self.id}: no step {step_id!r}")

    # --- the Lean files the platform writes itself -------------------------------

    def preamble_source(self) -> str:
        """`ZtPreamble.lean`: the imports and the trusted definitions, shared by the
        statement and by every attempt."""
        lines = [f"import {m}" for m in self.imports]
        body = self.preamble.strip()
        return "\n".join(lines) + ("\n\n" if lines else "") + (body + "\n" if body else "")

    def statement_source(self, step: Step) -> str:
        """`ZtStatement.lean`: the pinned statement with its proof left out. The
        `sorry` is deliberate and never leaves this file; the gate compares types."""
        if not step.formalised:
            raise ProblemError(f"{self.id}/{step.id} has no Lean statement yet")
        return f"import ZtPreamble\n\ntheorem {step.theorem} {step.signature} := sorry\n"

    def statement_sha256(self, step: Step) -> str:
        h = hashlib.sha256()
        h.update(self.preamble_source().encode())
        h.update(b"\0")
        h.update(self.statement_source(step).encode())
        return h.hexdigest()


def _text(table: dict, key: str, where: str, *, required: bool = True) -> str:
    value = table.get(key, "")
    if not isinstance(value, str):
        raise ProblemError(f"{where}: {key} must be a string")
    if required and not value.strip():
        raise ProblemError(f"{where}: {key} is required")
    return value.strip()


def load_problem(path: Path) -> Problem:
    file = path / "problem.toml" if path.is_dir() else path
    where = str(file)
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ProblemError(f"{where}: {exc}") from exc

    pid = _text(data, "id", where)
    if not _ID.match(pid):
        raise ProblemError(f"{where}: id {pid!r} must match {_ID.pattern}")
    if file.parent.name != pid:
        raise ProblemError(f"{where}: id {pid!r} must equal the directory name")
    status = _text(data, "status", where)
    if status not in {"known", "open"}:
        raise ProblemError(f"{where}: status must be 'known' or 'open'")
    imports = data.get("imports", [])
    if not isinstance(imports, list) or not all(
        isinstance(m, str) and _LEAN_NAME.match(m) for m in imports
    ):
        raise ProblemError(f"{where}: imports must be a list of Lean module names")

    steps: list[Step] = []
    seen_ids: set[str] = set()
    seen_theorems: set[str] = set()
    for i, raw in enumerate(data.get("steps", [])):
        swhere = f"{where}: steps[{i}]"
        sid = _text(raw, "id", swhere)
        if not _ID.match(sid) or sid in seen_ids:
            raise ProblemError(f"{swhere}: id {sid!r} is invalid or duplicated")
        seen_ids.add(sid)
        theorem = _text(raw, "theorem", swhere, required=False) or None
        signature = _text(raw, "signature", swhere, required=False) or None
        if bool(theorem) != bool(signature):
            raise ProblemError(f"{swhere}: give both theorem and signature, or neither")
        if theorem:
            if not _LEAN_NAME.match(theorem) or theorem in seen_theorems:
                raise ProblemError(f"{swhere}: theorem name {theorem!r} is invalid or duplicated")
            seen_theorems.add(theorem)
        steps.append(
            Step(
                id=sid,
                part=_text(raw, "part", swhere, required=False),
                informal=_text(raw, "informal", swhere),
                theorem=theorem,
                signature=signature,
                formalised_by=_text(raw, "formalised_by", swhere, required=bool(theorem)),
                reviewed_by=_text(raw, "reviewed_by", swhere, required=False),
            )
        )
    if not steps:
        raise ProblemError(f"{where}: a problem needs at least one step")

    return Problem(
        id=pid,
        title=_text(data, "title", where),
        source=_text(data, "source", where),
        status=status,
        informal=_text(data, "informal", where),
        imports=tuple(imports),
        preamble=_text(data, "preamble", where, required=False),
        steps=tuple(steps),
    )


def load_all(root: Path) -> list[Problem]:
    problems = [load_problem(p) for p in sorted(root.iterdir()) if (p / "problem.toml").is_file()]
    return problems
