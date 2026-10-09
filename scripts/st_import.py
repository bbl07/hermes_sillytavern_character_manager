"""Conservative SillyTavern import validation.

The permissive extractor in `png_metadata.parse_png_card` is for tooling
(accepts zTXt, raw JSON, case-sensitive keywords, last-wins). This module is
a **conservative V2 compatibility validator** modeled on SillyTavern's reader
([character-card-parser.js](https://github.com/SillyTavern/SillyTavern/blob/release/src/character-card-parser.js)):

- Reads `tEXt` chunks ONLY (no zTXt, no iTXt).
- Matches keywords case-insensitively.
- Checks `ccv3` FIRST; falls back to `chara`.
- Returns the FIRST matching chunk (first-wins, not last-wins).
- Decodes the text as base64 (strict `validate=True`). SillyTavern uses Node's
  `Buffer.from(text, 'base64')`, which is MORE permissive, so this validator
  may reject payloads that SillyTavern itself would accept. Payloads this
  validator accepts are canonical base64 that SillyTavern will also decode.

`validate_st_import_png` follows that contract, plus a real pixel
decode via Pillow (`Image.load()`), because a metadata-only or corrupt-image
PNG is not importable in practice.

Dependencies: Pillow (required for the pixel-decode step). The pipeline's
JSON/PNG-metadata checks remain stdlib-only; this module is the "import-grade"
validator.

Note: this is a conservative compatibility validator, not an exact importer
clone — Node's `Buffer.from(text, 'base64')` is more permissive than Python's
strict `validate=True` base64 decode.
"""
from __future__ import annotations

import base64
import json
from typing import Optional

from png_metadata import PngError, PNG_SIGNATURE, read_chunks



class STImportError(ValueError):
    """Raised when a PNG would not import into SillyTavern as-is."""


def _first_text_payload_st(blob: bytes) -> tuple[str, str]:
    """Mirror SillyTavern's text-chunk scan: tEXt only, case-insensitive,
    ccv3-before-chara, first match wins."""
    if blob[:8] != PNG_SIGNATURE:
        raise STImportError("not a PNG file (bad signature)")
    chunks: list[tuple[bytes, bytes]] = []  # (keyword, text)
    try:
        for ch in read_chunks(blob):
            if ch.type != b"tEXt":
                continue  # SillyTavern ignores zTXt/iTXt on read
            if b"\x00" not in ch.data:
                continue
            kw, payload = ch.data.split(b"\x00", 1)
            chunks.append((kw, payload))
    except PngError as exc:
        raise STImportError(f"PNG structure invalid: {exc}") from exc
    if not chunks:
        raise STImportError("PNG metadata does not contain any tEXt chunks")

    def find_first(keyword: str) -> Optional[tuple[bytes, bytes]]:
        for kw, payload in chunks:
            if kw.decode("utf-8", "replace").lower() == keyword:
                return kw, payload
        return None

    for kw_name in ("ccv3", "chara"):
        hit = find_first(kw_name)
        if hit is not None:
            return kw_name, hit[1].decode("utf-8", errors="replace")
    raise STImportError(
        "PNG metadata does not contain any character data "
        "(no tEXt chunk with keyword 'ccv3' or 'chara', case-insensitive)"
    )


def parse_card_st_strict(blob: bytes) -> dict:
    """Parse the card exactly as SillyTavern would: tEXt, base64, JSON."""
    keyword, text = _first_text_payload_st(blob)
    try:
        decoded = base64.b64decode(text, validate=True).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise STImportError(
            f"'{keyword}' payload is not valid base64-encoded UTF-8 JSON: {exc}"
        ) from exc
    try:
        card = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise STImportError(f"decoded '{keyword}' payload is not valid JSON: {exc}") from exc
    if not isinstance(card, dict):
        raise STImportError(f"'{keyword}' payload is not a JSON object")
    return card


def decode_pixels_with_pillow(blob: bytes) -> tuple[int, int]:
    """Force a full pixel decode with Pillow. Raises if the image is unusable."""
    try:
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise STImportError(
            "Pillow is required for import-grade pixel validation: pip install Pillow"
        ) from exc
    import io

    try:
        with Image.open(io.BytesIO(blob)) as img:
            img.load()  # force full decode; raises on corrupt IDAT
            return img.size
    except STImportError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise STImportError(f"image pixels failed to decode: {exc}") from exc


def validate_st_import_png(blob: bytes) -> dict:
    """Conservative SillyTavern-import-grade check. Returns diagnostics; raises on failure.

    Checks:
    1. ST parser semantics (tEXt-only, case-insensitive keyword, ccv3 first,
       first-match-wins, strict base64).
    2. Pixel decode with Pillow (`Image.load()`).
    3. V2 schema via validate_card_json.
    """
    from validate_cards import validate_card_json

    card = parse_card_st_strict(blob)
    size = decode_pixels_with_pillow(blob)
    report = validate_card_json(card)
    if not report.ok:
        raise STImportError(
            "embedded card fails V2 schema: " + "; ".join(report.errors)
        )
    return {
        "ok": True,
        "keyword": "ccv3" if _has_keyword(blob, "ccv3") else "chara",
        "image_size": size,
        "warnings": report.warnings,
        "card": card,
    }


def _has_keyword(blob: bytes, keyword: str) -> bool:
    try:
        _first_text_payload_st.__self__  # noqa: B018 - guard, not used
    except AttributeError:
        pass
    try:
        kw_name, _ = _first_text_payload_st(blob)
    except STImportError:
        return False
    return kw_name == keyword
