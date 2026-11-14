"""Thin, boring wrappers around the Lean 4 toolchain.

Everything here runs a subprocess and reports what happened. No decision about
whether a proof is acceptable is taken in this module.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_SOURCE = REPO_ROOT / "lean" / "Gate.lean"

# The untrusted Lean process gets this environment and nothing else. In particular
# it never sees an API key: elaborating a Lean file can run arbitrary code.
_PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")


class ToolchainError(RuntimeError):
    """Lean is missing or misbehaving. An infrastructure failure, not a verdict."""


@dataclass
class Completed:
    argv: list[str]
    returncode: int | None  # None means the process was killed on timeout
    stdout: str
    stderr: str
    seconds: float

    @property
    def timed_out(self) -> bool:
        return self.returncode is None


@dataclass
class Diagnostic:
    severity: str
    line: int
    column: int
    message: str

    def render(self) -> str:
        return f"{self.line}:{self.column}: {self.severity}: {self.message}"


@dataclass
class Compilation:
    process: Completed
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.process.returncode == 0 and not self.errors

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]


@lru_cache(maxsize=1)
def toolchain_bin() -> Path:
    """The `bin` directory of the toolchain pinned in `lean-toolchain`.

    Asked once, from the repository root, so that elan (when it is what `lean` on
    PATH really is) picks the pinned version. Every later call uses these binaries
    by absolute path, whatever directory it runs in.
    """
    found = shutil.which("lean")
    if found is None:
        raise ToolchainError("`lean` is not on PATH; install the toolchain named in lean-toolchain")
    out = subprocess.run([found, "--print-prefix"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    prefix = Path(out.stdout.strip()) / "bin"
    if out.returncode != 0 or not (prefix / "lean").exists():
        raise ToolchainError(f"`lean --print-prefix` failed: {out.stderr.strip() or out.stdout.strip()}")
    return prefix


def _tool(name: str) -> str:
    path = toolchain_bin() / name
    if not path.exists():
        raise ToolchainError(f"`{name}` is missing from the toolchain at {toolchain_bin()}")
    return str(path)


@lru_cache(maxsize=1)
def lean_version() -> str:
    out = subprocess.run([_tool("lean"), "--version"], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise ToolchainError(f"`lean --version` failed: {out.stderr.strip()}")
    return out.stdout.strip()


@lru_cache(maxsize=1)
def package_lean_path() -> str:
    """LEAN_PATH for the pinned packages (Mathlib and friends), as Lake computes it."""
    out = subprocess.run(
        [_tool("lake"), "env", "printenv", "LEAN_PATH"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise ToolchainError(f"`lake env` failed: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout.strip()


@lru_cache(maxsize=1)
def mathlib_revision() -> str | None:
    manifest = REPO_ROOT / "lake-manifest.json"
    if not manifest.is_file():
        return None
    for pkg in json.loads(manifest.read_text())["packages"]:
        if pkg["name"] == "mathlib":
            return f"{pkg.get('inputRev') or ''} ({pkg['rev']})".strip()
    return None


def lean_env(workdir: Path, *, packages: bool) -> dict[str, str]:
    env = {k: os.environ[k] for k in _PASSTHROUGH if k in os.environ}
    paths = [str(workdir)]
    if packages:
        paths.append(package_lean_path())
    env["LEAN_PATH"] = os.pathsep.join(paths)
    return env


def run(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float) -> Completed:
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False
        )
        return Completed(argv, proc.returncode, proc.stdout, proc.stderr, time.monotonic() - start)
    except subprocess.TimeoutExpired as exc:
        def text(raw: bytes | str | None) -> str:
            return raw.decode(errors="replace") if isinstance(raw, bytes) else (raw or "")
        return Completed(argv, None, text(exc.stdout), text(exc.stderr), time.monotonic() - start)


def compile_module(workdir: Path, module: str, *, packages: bool, timeout: float) -> Compilation:
    """Compile `<module>.lean` in `workdir` to `<module>.olean`."""
    proc = run(
        [_tool("lean"), "--json", "-o", f"{module}.olean", f"{module}.lean"],
        cwd=workdir, env=lean_env(workdir, packages=packages), timeout=timeout,
    )
    diagnostics: list[Diagnostic] = []
    for raw in proc.stdout.splitlines():
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue  # output printed by the file itself; kept in the raw log
        if not isinstance(msg, dict) or "severity" not in msg:
            continue
        pos = msg.get("pos") or {}
        diagnostics.append(
            Diagnostic(str(msg["severity"]), int(pos.get("line", 0)), int(pos.get("column", 0)),
                       str(msg.get("data", "")).strip())
        )
    return Compilation(proc, diagnostics)


def run_gate(workdir: Path, theorem: str, *, packages: bool, timeout: float) -> Completed:
    """Run lean/Gate.lean on the two compiled modules. It prints one JSON object."""
    return run(
        [_tool("lean"), "--run", str(GATE_SOURCE), "ZtStatement", "ZtAttempt", theorem],
        cwd=workdir, env=lean_env(workdir, packages=packages), timeout=timeout,
    )


def run_replay(workdir: Path, *, packages: bool, timeout: float) -> Completed:
    """Replay every declaration of the attempt through the kernel with `leanchecker`.
    This is what catches a file that slipped a declaration past the kernel."""
    return run(
        [_tool("leanchecker"), "ZtAttempt"],
        cwd=workdir, env=lean_env(workdir, packages=packages), timeout=timeout,
    )
