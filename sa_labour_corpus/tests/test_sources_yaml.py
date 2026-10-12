"""Source-registry policy tests.

The important one is that a paid legal publisher can never become a crawl
target. That is enforced as a hard error, and this test is the guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from labour_corpus.config import (
    PAID_HOST_MARKERS,
    ConfigError,
    PaidSourceError,
    assert_not_paid_host,
)
from labour_corpus.sources.registry import Access, load_registry

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "sources.yaml"

# One well-formed entry, shaped like the D-012 CCMA sources (8-15 s delays).
GOOD_ENTRY = {
    "id": "ccma_test", "name": "CCMA test", "host": "www.ccma.org.za",
    "base_url": "https://www.ccma.org.za/ccma-resources/", "forum": "CCMA_PUBLIC",
    "doc_type": "other", "kind": "link_index", "access": "open", "enabled": "true",
    "license_note": "test", "min_delay": "8.0", "max_delay": "15.0",
}


def registry_file(tmp_path: Path, *, version: str = "1", defaults: dict | None = None,
                  **overrides) -> Path:
    """A one-entry sources.yaml; an override of None drops that key."""
    fields = {**GOOD_ENTRY, **overrides}
    body = "\n".join(f"    {k}: {v}" for k, v in fields.items() if v is not None)
    text = f"version: {version}\n"
    if defaults:
        text += "defaults:\n" + "".join(f"  {k}: {v}\n" for k, v in defaults.items())
    text += f"sources:\n  - {body.lstrip()}\n"
    path = tmp_path / "sources.yaml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def registry():
    return load_registry(REGISTRY_PATH)


# -- the paid-source guard -------------------------------------------------
@pytest.mark.parametrize("host", [
    "www.juta.co.za",
    "www.lexisnexis.co.za",
    "discover.sabinet.co.za",
    "www.sallr.co.za",
    "uk.practicallaw.thomsonreuters.com",
    "labouredge.co.za",
    "westlaw.com",
])
def test_paid_publishers_are_a_hard_error(host):
    with pytest.raises(PaidSourceError):
        assert_not_paid_host(host)


def test_registry_contains_no_paid_hostname(registry):
    """Acceptance criterion 3: no paid host may appear as a crawl target."""
    for source in registry.sources:
        lowered = source.host.lower()
        for marker in PAID_HOST_MARKERS:
            assert marker not in lowered, (
                f"source {source.id!r} points at paid publisher host {source.host!r}"
            )


def test_registry_rejects_a_paid_host_at_load_time(tmp_path):
    bad = tmp_path / "bad_sources.yaml"
    bad.write_text(
        "version: 1\n"
        "sources:\n"
        "  - id: sneaky\n"
        "    name: Sneaky\n"
        "    host: discover.sabinet.co.za\n"
        "    base_url: https://discover.sabinet.co.za/\n"
        "    forum: X\n"
        "    doc_type: award\n"
        "    kind: link_index\n"
        "    access: open\n",
        encoding="utf-8",
    )
    with pytest.raises(PaidSourceError):
        load_registry(bad)


# -- strict loading: a typo is an error, never a silent default -------------
def test_a_well_formed_entry_loads(tmp_path):
    source = load_registry(registry_file(tmp_path)).sources[0]
    assert source.enabled is True and (source.min_delay, source.max_delay) == (8.0, 15.0)


@pytest.mark.parametrize("overrides, message", [
    # Without the check, `enable: false` left the source enabled (default True).
    ({"enabled": None, "enable": "false"}, r"unknown key\(s\): enable$"),
    # Without the check, `min-delay: 8` cut the D-012 politeness delay to 1 s.
    ({"min_delay": None, "min-delay": "8"}, r"unknown key\(s\): min-delay$"),
    ({"enabled": None}, "enabled: true or false"),
    ({"enabled": '"false"'}, "enabled: true or false"),   # a string, which bool() reads as True
    ({"min_delay": "9", "max_delay": "8"}, "min_delay 9.0 above max_delay 8.0"),
    ({"min_delay": "eight"}, "could not convert"),
    ({"min_delay": ""}, "NoneType"),                       # `min_delay:` with no value
])
def test_registry_typos_are_config_errors(tmp_path, overrides, message):
    with pytest.raises(ConfigError, match=message):
        load_registry(registry_file(tmp_path, **overrides))


def test_registry_refuses_an_unknown_defaults_key_and_an_unsupported_version(tmp_path):
    with pytest.raises(ConfigError, match=r"under defaults: min-delay$"):
        load_registry(registry_file(tmp_path, defaults={"jurisdiction": "ZA", "min-delay": "3"}))
    with pytest.raises(ConfigError, match="version 2 is not supported"):
        load_registry(registry_file(tmp_path, version="2"))


def test_public_hosts_only(registry):
    """Every registered host must be a public body, court service or regulator."""
    allowed_suffixes = (".gov.za", ".org.za", ".saflii.org", ".co.za")
    for source in registry.sources:
        assert source.host.endswith(allowed_suffixes), (
            f"unexpected host {source.host!r} for source {source.id!r}"
        )


# -- registry integrity ----------------------------------------------------
def test_every_source_has_the_required_fields(registry):
    for source in registry.sources:
        assert source.id
        assert source.host
        assert source.base_url.startswith("https://")
        assert source.forum
        assert source.license_note, f"{source.id} has no licence note"


def test_source_ids_are_unique(registry):
    ids = [s.id for s in registry.sources]
    assert len(ids) == len(set(ids))


def test_blocked_sources_are_registered_not_deleted(registry):
    """Blocked sources stay registered so the gap is reported, not hidden."""
    blocked = [s for s in registry.sources if s.access is Access.BOT_MANAGEMENT_BLOCKED]
    assert blocked, "a blocked source must stay registered so the gap is reported"
    # Match on host, not id: "saflii_zaccma" contains "ccma" and would otherwise
    # make this assertion pass for the wrong reason.
    assert any(s.host == "www.saflii.org" for s in blocked)


def test_content_signal_opt_out_source_is_disabled(registry):
    """A host reserving rights against AI input must not be enabled."""
    for source in registry.sources:
        if source.access is Access.CONTENT_SIGNAL_OPT_OUT:
            assert not source.enabled, f"{source.id} opted out but is enabled"
            assert not source.is_collectable


def test_at_least_one_source_is_collectable(registry):
    assert any(s.is_collectable for s in registry.sources)


def test_labour_court_forums_are_registered(registry):
    forums = {s.forum for s in registry.sources}
    for required in ("ZALCJHB", "ZALCCT", "ZALCD", "ZALAC", "ZACC", "ZACCMA"):
        assert required in forums, f"forum {required} is not registered"


def test_selecting_an_unknown_source_raises(registry):
    from labour_corpus.config import ConfigError

    with pytest.raises(ConfigError):
        registry.select(["no_such_source"])


def test_yaml_is_wellformed():
    data = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert isinstance(data["sources"], list)
