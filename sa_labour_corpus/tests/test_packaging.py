"""Declared dependencies match what the code actually uses.

Every runtime dependency is a package that has to be upgraded and audited, so
one the code never imports is pure supply-chain cost. The guarantees under test
are that each core dependency is imported somewhere in the package (or is a
documented transitive pin), that requirements.txt pins exactly what
pyproject.toml declares, and that the test pin stays past a known advisory.
"""

from __future__ import annotations

import importlib.metadata
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "labour_corpus"

# Distribution name -> import name, where they differ beyond case.
IMPORT_NAMES = {"PyYAML": "yaml", "python-docx": "docx", "beautifulsoup4": "bs4"}

# Pinned for reproducibility although no module imports them directly.
# Each is required by another core dependency, named here.
TRANSITIVE_PINS = {"lxml": "python-docx"}


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _name(spec: str) -> str:
    return re.split(r"[=<>!~;\[ ]", spec, maxsplit=1)[0]


def _module(distribution: str) -> str:
    return IMPORT_NAMES.get(distribution, distribution.lower().replace("-", "_"))


def _package_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="ignore") for path in PACKAGE.rglob("*.py")
    )


def _imports(module: str, source: str) -> bool:
    return re.search(rf"^\s*(?:import|from)\s+{re.escape(module)}\b", source, re.M) is not None


def _requirement_lines() -> set[str]:
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return {line.split("#", 1)[0].strip() for line in lines} - {""}


def test_every_core_dependency_is_imported_or_a_documented_transitive_pin():
    source = _package_source()
    unused = [
        spec for spec in _project()["dependencies"]
        if _name(spec) not in TRANSITIVE_PINS and not _imports(_module(_name(spec)), source)
    ]
    assert unused == [], f"declared but never imported under src/labour_corpus: {unused}"


@pytest.mark.parametrize("pin, required_by", sorted(TRANSITIVE_PINS.items()))
def test_transitive_pins_are_really_transitive(pin, required_by):
    # Imported directly, it is a real dependency and leaves the allow-list.
    assert not _imports(_module(pin), _package_source())
    declared = {_name(spec) for spec in _project()["dependencies"]}
    assert pin in declared and required_by in declared
    try:
        requires = importlib.metadata.requires(required_by) or []
    except importlib.metadata.PackageNotFoundError:
        pytest.skip(f"{required_by} is not installed here")
    assert pin in {_name(r).lower() for r in requires}


def test_requirements_pin_exactly_what_pyproject_declares():
    project = _project()
    declared = set(project["dependencies"])
    for extra in project["optional-dependencies"].values():
        declared.update(extra)
    assert _requirement_lines() == declared


def test_pytest_pin_is_past_the_tmp_directory_advisory():
    # GHSA-6w46-j5rx-g56g / CVE-2025-71176: pytest through 9.0.2 trusts a
    # predictable /tmp/pytest-of-{user} directory on UNIX. Fixed in 9.0.3.
    pins = [s for s in _project()["optional-dependencies"]["test"] if _name(s) == "pytest"]
    assert len(pins) == 1
    pinned = pins[0].split("==", 1)[1].strip() if "==" in pins[0] else ""
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(.*)", pinned)
    assert match, f"pytest is not pinned to a release: {pins[0]!r}"
    release = tuple(int(part) for part in match[1].split("."))
    # A pre-release of 9.0.3 itself predates the fix.
    prerelease = re.match(r"[.\-_]?(a|b|rc|dev)", match[2], re.I) is not None
    assert release > (9, 0, 3) or (release == (9, 0, 3) and not prerelease)


def test_tls_chain_completion_dependencies_are_declared():
    # tls.py degrades silently without cryptography, so the dependency must be
    # declared rather than merely present in one environment.
    declared = {_name(spec) for spec in _project()["dependencies"]}
    assert {"certifi", "cryptography"} <= declared
