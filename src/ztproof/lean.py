"""Thin, boring wrappers around the Lean 4 toolchain.

Everything here runs a subprocess and reports what happened. No decision about
whether a proof is acceptable is taken in this module.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
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
    max_rss_mb: float = 0.0  # peak resident memory of the process

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
    # A Lean program may itself look for `lean`: `lean/Gate.lean` asks for the
    # sysroot, which Lean finds by running `lean --print-prefix` from PATH. Under
    # elan that is a proxy, and in a temporary directory the proxy knows no
    # toolchain. So the pinned toolchain is named outright, and comes first on PATH.
    env["LEAN_SYSROOT"] = str(toolchain_bin().parent)
    env["PATH"] = os.pathsep.join([str(toolchain_bin()), env.get("PATH", "")]).rstrip(os.pathsep)
    paths = [str(workdir)]
    if packages:
        paths.append(package_lean_path())
    env["LEAN_PATH"] = os.pathsep.join(paths)
    return env


def run(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float) -> Completed:
    """Run one toolchain process to the end, or kill it at `timeout`.

    The process gets its own session, so a timeout kills whatever it started as
    well. Its peak resident memory comes from the kernel's accounting for that one
    child (`wait4`), which is what Stage 0 uses to size the pool of Lean workers.
    """
    start = time.monotonic()
    timed_out = threading.Event()
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                start_new_session=True)

        def kill() -> None:
            timed_out.set()
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        timer = threading.Timer(timeout, kill)
        timer.start()
        try:
            _, status, usage = os.wait4(proc.pid, 0)
        finally:
            timer.cancel()
        proc.returncode = os.waitstatus_to_exitcode(status)  # reaped here; Popen must not wait again
        out.seek(0)
        err.seek(0)
        stdout, stderr = out.read().decode(errors="replace"), err.read().decode(errors="replace")
    return Completed(
        argv, None if timed_out.is_set() else proc.returncode, stdout, stderr, time.monotonic() - start,
        max_rss_mb=round(usage.ru_maxrss / 1024, 1),  # Linux reports kilobytes
    )


def compile_module(workdir: Path, module: str, *, packages: bool, timeout: float,
                   options: dict[str, int] | None = None) -> Compilation:
    """Compile `<module>.lean` in `workdir` to `<module>.olean`.

    `options` sets defaults of Lean options for this file, as `-Dname=value`.
    """
    defaults = [f"-D{name}={value}" for name, value in (options or {}).items()]
    proc = run(
        [_tool("lean"), *defaults, "--json", "-o", f"{module}.olean", f"{module}.lean"],
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
