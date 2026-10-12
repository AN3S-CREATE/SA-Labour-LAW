"""Download: store original bytes with a checksum sidecar, never overwriting.

Layout, one directory per record::

    data/ccma/raw/{source}/{native_id}/original.{ext}
    data/ccma/raw/{source}/{native_id}/original.{ext}.sha256
    data/ccma/raw/{source}/{native_id}/meta.json
    data/ccma/raw/{source}/{native_id}/quarantine/{sha256[:16]}.{ext}   (conflicts)

Same checksum -> unchanged, nothing written. Different checksum -> the new
bytes go to quarantine, the held original is untouched, and the caller writes
a ``checksum_conflict`` error row. A human decides which version is right.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..parse.office import SniffedFormat, sniff_format
from .catalogue import now_iso
from .paths import CcmaPaths
from .troubleshoot import check_disk

_EXT_BY_FORMAT = {
    SniffedFormat.PDF: "pdf",
    SniffedFormat.OLE2_DOC: "doc",
    SniffedFormat.OOXML: "docx",
    SniffedFormat.RTF: "rtf",
}


def extension_for(body: bytes, content_type: str | None, url: str) -> str:
    sniffed = sniff_format(body)
    if sniffed in _EXT_BY_FORMAT:
        return _EXT_BY_FORMAT[sniffed]
    lowered = (content_type or "").lower()
    if "html" in lowered or url.lower().endswith((".html", ".htm")):
        return "html"
    if url.lower().endswith(".txt") or "text/plain" in lowered:
        return "txt"
    return "bin"


@dataclass(slots=True)
class StoreOutcome:
    status: str          # stored | unchanged | quarantined
    path: Path
    sha256: str
    retrieved_at: str
    held_sha256: str | None = None


def find_original(record_dir: Path) -> Path | None:
    for path in sorted(record_dir.glob("original.*")):
        if not path.name.endswith((".sha256", ".json")):
            return path
    return None


def read_sidecar(original: Path) -> str | None:
    sidecar = original.with_name(original.name + ".sha256")
    if not sidecar.exists():
        return None
    return sidecar.read_text(encoding="utf-8").split()[0].strip()


def store_artifact(
    paths: CcmaPaths, source: str, native_id: str, body: bytes,
    content_type: str | None, url: str, *, check_space: bool = True,
) -> StoreOutcome:
    if check_space:
        check_disk(paths.data)
    digest = hashlib.sha256(body).hexdigest()
    record_dir = paths.record_dir(source, native_id)
    retrieved_at = now_iso()
    existing = find_original(record_dir)

    if existing is not None:
        held = read_sidecar(existing) or hashlib.sha256(existing.read_bytes()).hexdigest()
        if held == digest:
            return StoreOutcome("unchanged", existing, digest, retrieved_at, held)
        ext = extension_for(body, content_type, url)
        quarantine = record_dir / "quarantine"
        quarantine.mkdir(parents=True, exist_ok=True)
        target = quarantine / f"{digest[:16]}.{ext}"
        target.write_bytes(body)
        (quarantine / f"{digest[:16]}.json").write_text(json.dumps({
            "source_url": url, "content_type": content_type, "sha256": digest,
            "retrieved_at": retrieved_at, "held_sha256": held,
            "reason": "checksum differs from the held original",
        }, indent=2), encoding="utf-8")
        return StoreOutcome("quarantined", target, digest, retrieved_at, held)

    record_dir.mkdir(parents=True, exist_ok=True)
    ext = extension_for(body, content_type, url)
    target = record_dir / f"original.{ext}"
    target.write_bytes(body)
    target.with_name(target.name + ".sha256").write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    (record_dir / "meta.json").write_text(json.dumps({
        "source": source, "native_id": native_id, "source_url": url,
        "content_type": content_type, "sha256": digest, "bytes": len(body),
        "retrieved_at": retrieved_at,
    }, indent=2), encoding="utf-8")
    return StoreOutcome("stored", target, digest, retrieved_at)


def read_meta(record_dir: Path) -> dict:
    meta = record_dir / "meta.json"
    return json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
