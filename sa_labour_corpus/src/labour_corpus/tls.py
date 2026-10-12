"""Completing an incomplete TLS certificate chain, without weakening verification.

Some government and public-body sites serve only their leaf certificate and
omit the intermediate CA. Browsers and curl paper over this by fetching the
missing intermediate from the Authority Information Access (AIA) extension;
Python's ssl module does not, so httpx fails with
``unable to get local issuer certificate`` on a site a browser opens happily.

``www.ccma.org.za`` is exactly that case: openssl reports
``Verify return code: 21 (unable to verify the first certificate)``.

This module does what a browser does, and no more:

1. Read the server's certificate. The one unverified connection made here
   exchanges **no application data** -- no request is sent, no response body is
   read. It exists only to obtain the certificate the server is publishing.
2. Read the AIA "CA Issuers" URL out of that certificate and fetch the
   intermediate over plain HTTP, which is how AIA is specified.
3. **Verify the fetched intermediate chains to a root already trusted by
   certifi**: it must be a current CA certificate whose signature verifies
   under that root's public key. A matching issuer *name* proves nothing --
   anyone can copy a root's subject -- so an intermediate whose signature does
   not verify is discarded.
4. Append it to a local bundle, and use that bundle for every real request.

Verification is never disabled and no host is ever exempted from it. The result
is a complete chain that validates to a trusted root exactly as it should have
in the first place.
"""

from __future__ import annotations

import re
import socket
import ssl
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import certifi

CHAIN_TIMEOUT = 20
MAX_INTERMEDIATE_BYTES = 64 * 1024

# The error text that means "the server did not send its intermediate".
INCOMPLETE_CHAIN_MARKERS = (
    "unable to get local issuer certificate",
    "certificate verify failed",
)


@dataclass(slots=True)
class ChainResult:
    host: str
    completed: bool = False
    bundle_path: Path | None = None
    issuer_url: str | None = None
    subject: str | None = None
    detail: str = ""


def looks_like_incomplete_chain(error: BaseException | str) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in INCOMPLETE_CHAIN_MARKERS)


def _fetch_leaf_certificate(host: str, port: int = 443) -> bytes | None:
    """Read the server's leaf certificate. Sends no application data."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=CHAIN_TIMEOUT) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                return tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError):
        return None


def _issuer_url_from_certificate(der: bytes) -> tuple[str | None, str | None]:
    """Return (ca_issuers_url, subject) read from the certificate's AIA extension."""
    try:
        from cryptography import x509
        from cryptography.x509.oid import AuthorityInformationAccessOID
    except ImportError:
        return None, None

    try:
        certificate = x509.load_der_x509_certificate(der)
        subject = certificate.subject.rfc4514_string()
        extension = certificate.extensions.get_extension_for_class(
            x509.AuthorityInformationAccess
        )
    except Exception:  # noqa: BLE001 - malformed certificates raise broadly
        return None, None

    for description in extension.value:
        if description.access_method == AuthorityInformationAccessOID.CA_ISSUERS:
            value = getattr(description.access_location, "value", None)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value, subject
    return None, subject


def _download(url: str) -> bytes | None:
    """Fetch an AIA intermediate. Plain HTTP is normal and expected here.

    The body is streamed and abandoned once it passes MAX_INTERMEDIATE_BYTES.
    A certificate is a few KiB, and the URL was read from a certificate taken
    over an unverified handshake, so the server is not trusted to keep the
    body small.
    """
    import httpx

    try:
        with httpx.stream(
            "GET", url, timeout=CHAIN_TIMEOUT, follow_redirects=True
        ) as response:
            if response.status_code != 200:
                return None
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_INTERMEDIATE_BYTES:
                return None
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_INTERMEDIATE_BYTES:
                    return None
                chunks.append(chunk)
            body = b"".join(chunks)
    except httpx.HTTPError:
        return None
    return body or None


def _to_pem(data: bytes) -> bytes | None:
    """Normalise a DER or PEM certificate to PEM."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives.serialization import Encoding
    except ImportError:
        return None
    for loader in (x509.load_der_x509_certificate, x509.load_pem_x509_certificate):
        try:
            return loader(data).public_bytes(Encoding.PEM)
        except Exception:  # noqa: BLE001
            continue
    return None


_PEM_BLOCK_RE = re.compile(
    rb"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.DOTALL)


def _trusted_roots(x509: Any) -> list[Any]:
    """certifi's roots, loaded one by one.

    A bulk load is all-or-nothing: certifi carries a root with a non-positive
    serial, which cryptography already warns about and says it will reject. If
    it ever raises, a bulk load would fail and the gate would refuse every
    intermediate, silently ending chain completion. Loading each root alone
    costs only the root that cannot be read. The warning concerns certifi's
    data, not the certificate being judged, so it is not repeated per call.
    """
    roots = []
    for block in _PEM_BLOCK_RE.findall(Path(certifi.where()).read_bytes()):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                roots.append(x509.load_pem_x509_certificate(block))
        except Exception:  # noqa: BLE001 - an unreadable root is skipped, never trusted
            continue
    return roots


def _intermediate_is_trusted(pem: bytes) -> bool:
    """True when the intermediate is a current CA signed directly by a certifi root.

    This is the security gate: an intermediate that does not chain to an
    already-trusted root is discarded rather than added to the bundle. Whatever
    is written to the bundle becomes a trust anchor for that host, so the
    root's public key must verify the candidate's signature; a matching issuer
    name alone is never enough, because anyone can copy a root's subject.
    """
    try:
        from cryptography import x509
        from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
    except ImportError:
        return False

    try:
        candidate = x509.load_pem_x509_certificate(pem)
        roots = _trusted_roots(x509)
        # It must be a CA certificate...
        constraints = candidate.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
        if not constraints.ca:
            return False
        # ...whose key may sign certificates, when it declares a key usage...
        try:
            usage = candidate.extensions.get_extension_for_class(x509.KeyUsage).value
        except x509.ExtensionNotFound:
            usage = None
        if usage is not None and not usage.key_cert_sign:
            return False
        # ...inside its validity period.
        now = datetime.now(timezone.utc)
        if not (candidate.not_valid_before_utc <= now <= candidate.not_valid_after_utc):
            return False
    except x509.ExtensionNotFound:  # no BasicConstraints: not a CA certificate
        return False
    except Exception:  # noqa: BLE001 - any failure means "do not trust"
        return False

    # Several roots can share a subject (re-keyed or cross-signed), so try each.
    for root in roots:
        try:
            if root.subject != candidate.issuer:
                continue
            candidate.verify_directly_issued_by(root)
        except (InvalidSignature, ValueError, TypeError, UnsupportedAlgorithm):
            continue
        return True
    return False


def complete_chain(host: str, cache_dir: Path, port: int = 443) -> ChainResult:
    """Try to build a full trust bundle for a host with a missing intermediate."""
    result = ChainResult(host=host)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    bundle = cache_dir / f"{host}.pem"

    if bundle.exists() and bundle.stat().st_size > 0:
        result.completed = True
        result.bundle_path = bundle
        result.detail = "using a previously completed chain for this host"
        return result

    der = _fetch_leaf_certificate(host, port)
    if der is None:
        result.detail = "could not read the server certificate"
        return result

    issuer_url, subject = _issuer_url_from_certificate(der)
    result.issuer_url = issuer_url
    result.subject = subject
    if not issuer_url:
        result.detail = "certificate publishes no AIA CA Issuers URL"
        return result

    raw = _download(issuer_url)
    if raw is None:
        result.detail = f"could not download the intermediate from {issuer_url}"
        return result

    pem = _to_pem(raw)
    if pem is None:
        result.detail = "downloaded AIA data is not a certificate"
        return result

    if not _intermediate_is_trusted(pem):
        result.detail = (
            "the downloaded intermediate does not chain to a trusted root; "
            "discarded rather than trusted"
        )
        return result

    bundle.write_bytes(Path(certifi.where()).read_bytes() + b"\n" + pem)
    result.completed = True
    result.bundle_path = bundle
    result.detail = (
        f"intermediate fetched from {issuer_url} and verified against certifi "
        "roots; full verification retained"
    )
    return result


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()
