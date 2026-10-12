"""TLS chain-completion tests.

The security property under test: verification is never disabled, and an
intermediate that does not chain to an already-trusted root is discarded
rather than used.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import certifi
import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

import labour_corpus.tls as tls
from labour_corpus.config import CorpusPaths
from labour_corpus.tls import (
    MAX_INTERMEDIATE_BYTES,
    ChainResult,
    _download,
    _intermediate_is_trusted,
    complete_chain,
    host_of,
    looks_like_incomplete_chain,
)

# The intermediate a real run completed for the CCMA (read only, if present).
CACHED_CCMA_BUNDLE = (
    Path(__file__).resolve().parents[1] / "data" / "corpus" / "db" / "tls" / "www.ccma.org.za.pem"
)


# -- certificates generated in memory ---------------------------------------
def _key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _certificate(
    subject: x509.Name,
    issuer: x509.Name,
    public_key: ec.EllipticCurvePublicKey,
    signing_key: ec.EllipticCurvePrivateKey,
    *,
    ca: bool | None = True,
    key_cert_sign: bool | None = None,
    valid_days: tuple[int, int] = (-1, 365),
) -> x509.Certificate:
    """A certificate; ``ca=None`` omits BasicConstraints, ``key_cert_sign=None`` KeyUsage."""
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + timedelta(days=valid_days[0]))
        .not_valid_after(now + timedelta(days=valid_days[1]))
    )
    if ca is not None:
        builder = builder.add_extension(
            x509.BasicConstraints(ca=ca, path_length=None), critical=True
        )
    if key_cert_sign is not None:
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=key_cert_sign,
                crl_sign=key_cert_sign,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    return builder.sign(signing_key, hashes.SHA256())


def _pem(*certificates: x509.Certificate) -> bytes:
    return b"".join(c.public_bytes(Encoding.PEM) for c in certificates)


def _certifi_roots() -> list[x509.Certificate]:
    with warnings.catch_warnings():
        # A few certifi roots carry non-positive serials; cryptography warns.
        warnings.simplefilter("ignore")
        return x509.load_pem_x509_certificates(Path(certifi.where()).read_bytes())


def _install_roots(tmp_path: Path, monkeypatch, *roots: x509.Certificate) -> None:
    """Make the given roots the whole of certifi's trust store for one test."""
    bundle = tmp_path / "roots.pem"
    bundle.write_bytes(_pem(*roots))
    monkeypatch.setattr(tls.certifi, "where", lambda: str(bundle))


@pytest.fixture
def test_root(tmp_path, monkeypatch):
    """A self-signed test root installed as certifi's only root: (name, key)."""
    key = _key()
    name = _name("Test Root CA")
    _install_roots(tmp_path, monkeypatch, _certificate(name, name, key.public_key(), key))
    return name, key


# -- detecting the condition ------------------------------------------------
@pytest.mark.parametrize("message", [
    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate",
    "unable to get local issuer certificate (_ssl.c:1032)",
    "certificate verify failed",
])
def test_incomplete_chain_is_recognised(message):
    assert looks_like_incomplete_chain(message) is True


@pytest.mark.parametrize("message", [
    "Connection timed out",
    "All connection attempts failed",
    "Server disconnected without sending a response",
    "hostname mismatch",
])
def test_unrelated_errors_are_not_treated_as_a_chain_problem(message):
    assert looks_like_incomplete_chain(message) is False


def test_host_of():
    assert host_of("https://www.ccma.org.za/a/b?c=d") == "www.ccma.org.za"


# -- the security gate ------------------------------------------------------
def test_an_untrusted_intermediate_is_discarded(tmp_path, monkeypatch):
    """An intermediate that does not chain to a trusted root must be refused."""
    import labour_corpus.tls as tls

    monkeypatch.setattr(tls, "_fetch_leaf_certificate", lambda h, p=443: b"der-bytes")
    monkeypatch.setattr(
        tls, "_issuer_url_from_certificate",
        lambda der: ("http://ca.example/inter.cer", "CN=example"),
    )
    monkeypatch.setattr(tls, "_download", lambda url: b"raw")
    monkeypatch.setattr(tls, "_to_pem", lambda raw: b"-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n")
    # The gate says "not trusted".
    monkeypatch.setattr(tls, "_intermediate_is_trusted", lambda pem: False)

    result = complete_chain("evil.example", tmp_path)
    assert result.completed is False
    assert "does not chain to a trusted root" in result.detail
    assert not (tmp_path / "evil.example.pem").exists()


def test_a_trusted_intermediate_is_written(tmp_path, monkeypatch):
    import labour_corpus.tls as tls

    monkeypatch.setattr(tls, "_fetch_leaf_certificate", lambda h, p=443: b"der")
    monkeypatch.setattr(
        tls, "_issuer_url_from_certificate",
        lambda der: ("http://ca.example/inter.cer", "CN=example"),
    )
    monkeypatch.setattr(tls, "_download", lambda url: b"raw")
    monkeypatch.setattr(tls, "_to_pem", lambda raw: b"-----BEGIN CERTIFICATE-----\nok\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr(tls, "_intermediate_is_trusted", lambda pem: True)

    result = complete_chain("good.example", tmp_path)
    assert result.completed is True
    assert result.bundle_path is not None and result.bundle_path.exists()
    assert "verified against certifi roots" in result.detail
    # The bundle must contain the certifi roots as well as the intermediate.
    body = result.bundle_path.read_bytes()
    assert b"-----BEGIN CERTIFICATE-----" in body
    assert len(body) > 1000


def test_no_aia_url_means_no_completion(tmp_path, monkeypatch):
    import labour_corpus.tls as tls

    monkeypatch.setattr(tls, "_fetch_leaf_certificate", lambda h, p=443: b"der")
    monkeypatch.setattr(tls, "_issuer_url_from_certificate", lambda der: (None, "CN=x"))

    result = complete_chain("noaia.example", tmp_path)
    assert result.completed is False
    assert "no AIA" in result.detail


def test_unreadable_certificate_means_no_completion(tmp_path, monkeypatch):
    import labour_corpus.tls as tls

    monkeypatch.setattr(tls, "_fetch_leaf_certificate", lambda h, p=443: None)
    result = complete_chain("down.example", tmp_path)
    assert result.completed is False
    assert "could not read" in result.detail


def test_a_cached_bundle_is_reused(tmp_path):
    bundle = tmp_path / "cached.example.pem"
    bundle.write_bytes(b"-----BEGIN CERTIFICATE-----\ncached\n-----END CERTIFICATE-----\n")
    result = complete_chain("cached.example", tmp_path)
    assert result.completed is True
    assert result.bundle_path == bundle
    assert "previously completed" in result.detail


# -- the gate itself, against real signatures --------------------------------
def test_a_forged_ca_that_copies_a_real_roots_name_is_refused():
    """Naming a trusted root as issuer proves nothing; the signature must verify.

    Whatever the gate accepts is written to the host's bundle and becomes a
    trust anchor for it, so this forgery would let an on-path attacker
    impersonate the host on every later run.
    """
    real_root = _certifi_roots()[0]
    key = _key()
    forged = _certificate(
        _name("Forged Intermediate CA"), real_root.subject, key.public_key(), key,
        key_cert_sign=True,
    )
    assert _intermediate_is_trusted(_pem(forged)) is False


def test_a_forged_ca_is_not_written_to_the_bundle(tmp_path, monkeypatch):
    """End to end through complete_chain, with the real gate in place."""
    real_root = _certifi_roots()[0]
    key = _key()
    forged = _certificate(
        _name("Forged Intermediate CA"), real_root.subject, key.public_key(), key,
    )
    monkeypatch.setattr(tls, "_fetch_leaf_certificate", lambda h, p=443: b"der")
    monkeypatch.setattr(
        tls, "_issuer_url_from_certificate",
        lambda der: ("http://attacker.example/inter.cer", "CN=www.example"),
    )
    monkeypatch.setattr(tls, "_download", lambda url: forged.public_bytes(Encoding.DER))

    result = complete_chain("victim.example", tmp_path)
    assert result.completed is False
    assert "does not chain to a trusted root" in result.detail
    assert not (tmp_path / "victim.example.pem").exists()


def test_an_intermediate_signed_by_a_trusted_root_is_accepted(test_root):
    root_name, root_key = test_root
    key = _key()
    intermediate = _certificate(
        _name("Test Intermediate CA"), root_name, key.public_key(), root_key,
        key_cert_sign=True,
    )
    assert _intermediate_is_trusted(_pem(intermediate)) is True


def test_one_unreadable_root_costs_only_that_root(tmp_path, monkeypatch):
    """certifi carries a root cryptography already warns about. If loading it
    ever raises, a bulk load would fail and refuse every intermediate; roots
    are loaded one by one so only the unreadable one is lost."""
    key = _key()
    name = _name("Test Root CA")
    root = _certificate(name, name, key.public_key(), key)
    garbage = (b"-----BEGIN CERTIFICATE-----\nTm90IGEgY2VydGlmaWNhdGU=\n"
               b"-----END CERTIFICATE-----\n")
    bundle = tmp_path / "roots.pem"
    bundle.write_bytes(garbage + _pem(root))
    monkeypatch.setattr(tls.certifi, "where", lambda: str(bundle))
    with pytest.raises(ValueError):          # what a bulk load would do
        x509.load_pem_x509_certificates(bundle.read_bytes())

    ikey = _key()
    intermediate = _certificate(
        _name("Test Intermediate CA"), name, ikey.public_key(), key, key_cert_sign=True,
    )
    assert _intermediate_is_trusted(_pem(intermediate)) is True


def test_a_root_name_with_the_wrong_signing_key_is_refused(test_root):
    root_name, _ = test_root
    key = _key()
    forged = _certificate(_name("Forged CA"), root_name, key.public_key(), _key())
    assert _intermediate_is_trusted(_pem(forged)) is False


def test_an_end_entity_certificate_is_refused(test_root):
    """Signed by the root, but ca=False: it may not issue certificates."""
    root_name, root_key = test_root
    key = _key()
    leaf = _certificate(_name("www.example"), root_name, key.public_key(), root_key, ca=False)
    assert _intermediate_is_trusted(_pem(leaf)) is False


def test_a_certificate_without_basic_constraints_is_refused(test_root):
    root_name, root_key = test_root
    key = _key()
    bare = _certificate(_name("Bare"), root_name, key.public_key(), root_key, ca=None)
    assert _intermediate_is_trusted(_pem(bare)) is False


def test_a_ca_whose_key_usage_forbids_signing_certificates_is_refused(test_root):
    root_name, root_key = test_root
    key = _key()
    intermediate = _certificate(
        _name("No Cert Sign CA"), root_name, key.public_key(), root_key,
        key_cert_sign=False,
    )
    assert _intermediate_is_trusted(_pem(intermediate)) is False


@pytest.mark.parametrize(
    "valid_days", [(-30, -1), (1, 30)], ids=["expired", "not-yet-valid"]
)
def test_an_intermediate_outside_its_validity_period_is_refused(test_root, valid_days):
    root_name, root_key = test_root
    key = _key()
    intermediate = _certificate(
        _name("Test Intermediate CA"), root_name, key.public_key(), root_key,
        valid_days=valid_days,
    )
    assert _intermediate_is_trusted(_pem(intermediate)) is False


def test_every_root_sharing_the_issuer_name_is_tried(tmp_path, monkeypatch):
    """A re-keyed root shares its subject with the old one; either may sign."""
    name = _name("Re-keyed Root CA")
    old_key, new_key = _key(), _key()
    _install_roots(
        tmp_path, monkeypatch,
        _certificate(name, name, old_key.public_key(), old_key),
        _certificate(name, name, new_key.public_key(), new_key),
    )
    key = _key()
    intermediate = _certificate(_name("Test Intermediate CA"), name, key.public_key(), new_key)
    assert _intermediate_is_trusted(_pem(intermediate)) is True


def test_unreadable_input_is_refused(test_root, tmp_path, monkeypatch):
    root_name, root_key = test_root
    assert _intermediate_is_trusted(b"not a certificate") is False

    key = _key()
    intermediate = _certificate(_name("Test Intermediate CA"), root_name, key.public_key(), root_key)
    monkeypatch.setattr(tls.certifi, "where", lambda: str(tmp_path / "missing.pem"))
    assert _intermediate_is_trusted(_pem(intermediate)) is False


def test_the_cached_ccma_intermediate_is_accepted():
    """The real intermediate a run completed (Certum) passes the stricter gate."""
    if not CACHED_CCMA_BUNDLE.is_file():
        pytest.skip("no cached CCMA chain in this checkout")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        certificates = x509.load_pem_x509_certificates(CACHED_CCMA_BUNDLE.read_bytes())
    # The bundle is certifi followed by the one fetched intermediate.
    assert _intermediate_is_trusted(_pem(certificates[-1])) is True


# -- downloading the intermediate ------------------------------------------
class _Body:
    """A streamed response body that records how much of it was read."""

    def __init__(self, total: int, chunk: int = 16 * 1024) -> None:
        self.total = total
        self.chunk = chunk
        self.consumed = 0

    def __iter__(self):
        while self.consumed < self.total:
            size = min(self.chunk, self.total - self.consumed)
            self.consumed += size
            yield b"\x00" * size


def _serve(monkeypatch, handler) -> httpx.Client:
    """Route module-level httpx.get and httpx.stream through a MockTransport."""
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "get", lambda url, **kw: client.get(url, **kw))
    monkeypatch.setattr(
        httpx, "stream", lambda method, url, **kw: client.stream(method, url, **kw)
    )
    return client


def test_an_oversized_intermediate_is_abandoned_without_reading_it_all(monkeypatch):
    body = _Body(1024 * 1024)
    client = _serve(monkeypatch, lambda request: httpx.Response(200, content=body))
    try:
        assert _download("http://ca.example/inter.cer") is None
    finally:
        client.close()
    assert body.consumed <= MAX_INTERMEDIATE_BYTES + body.chunk


def test_a_declared_oversized_intermediate_is_not_read(monkeypatch):
    body = _Body(1024 * 1024)
    client = _serve(
        monkeypatch,
        lambda request: httpx.Response(
            200, headers={"Content-Length": str(body.total)}, content=body
        ),
    )
    try:
        assert _download("http://ca.example/inter.cer") is None
    finally:
        client.close()
    assert body.consumed == 0


def test_a_non_200_intermediate_response_is_not_read(monkeypatch):
    body = _Body(1024 * 1024)
    client = _serve(monkeypatch, lambda request: httpx.Response(404, content=body))
    try:
        assert _download("http://ca.example/inter.cer") is None
    finally:
        client.close()
    assert body.consumed == 0


def test_an_intermediate_is_downloaded_through_a_redirect(monkeypatch):
    """Redirects are still followed, as before: CA AIA URLs do redirect."""
    der = b"\x30\x82" + b"\x01" * 1500

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old.cer":
            return httpx.Response(301, headers={"Location": "http://ca.example/new.cer"})
        return httpx.Response(200, content=der)

    client = _serve(monkeypatch, handler)
    try:
        assert _download("http://ca.example/old.cer") == der
    finally:
        client.close()


def test_an_empty_or_failed_download_gives_nothing(monkeypatch):
    client = _serve(monkeypatch, lambda request: httpx.Response(200, content=b""))
    try:
        assert _download("http://ca.example/inter.cer") is None
    finally:
        client.close()

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = _serve(monkeypatch, refuse)
    try:
        assert _download("http://ca.example/inter.cer") is None
    finally:
        client.close()


# -- the fetcher never disables verification --------------------------------
def test_fetcher_never_sets_verify_false():
    """The one property that must never regress."""
    source = Path(__file__).resolve().parents[1] / "src" / "labour_corpus"
    for module in ("http.py", "tls.py", "browser.py"):
        text = (source / module).read_text(encoding="utf-8")
        assert "verify=False" not in text, f"{module} disables TLS verification"
        assert "verify = False" not in text, f"{module} disables TLS verification"


def _offline_client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))


def test_chain_completion_is_attempted_once_per_host(tmp_path, monkeypatch):
    """A host that cannot be repaired must not be retried all run."""
    from labour_corpus.http import Fetcher

    calls: list[str] = []

    def fake_complete(host, cache_dir, port=443):
        calls.append(host)
        return ChainResult(host=host, completed=False, detail="no")

    monkeypatch.setattr("labour_corpus.tls.complete_chain", fake_complete)
    # A mock transport: no request is sent, and a default client's SSL setup
    # (seconds of certifi loading) is avoided.
    fetcher = Fetcher("UA", tls_cache_dir=tmp_path, client=_offline_client())
    assert fetcher._try_complete_chain("a.example") is False
    assert fetcher._try_complete_chain("a.example") is False
    assert calls == ["a.example"]
    fetcher.close()


def test_chain_completion_is_skipped_without_a_cache_dir(tmp_path):
    from labour_corpus.http import Fetcher

    fetcher = Fetcher("UA", client=_offline_client())  # no tls_cache_dir
    assert fetcher._try_complete_chain("a.example") is False
    fetcher.close()


def test_corpus_paths_expose_a_tls_cache(tmp_path):
    paths = CorpusPaths(root=tmp_path / "corpus")
    paths.ensure()
    assert paths.tls_cache.exists()
    assert paths.tls_cache.name == "tls"
