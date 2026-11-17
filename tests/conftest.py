import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

needs_lean = pytest.mark.skipif(shutil.which("lean") is None, reason="Lean toolchain not installed")


@pytest.fixture(scope="session")
def fixtures() -> Path:
    return FIXTURES
