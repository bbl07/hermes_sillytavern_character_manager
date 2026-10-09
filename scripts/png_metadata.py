"""PNG metadata codec for SillyTavern character cards (stdlib only).

Implements the exact convention SillyTavern uses to store card JSON in a PNG:
- Payload: UTF-8 JSON, base64-encoded.
- Chunk type: tEXt (uncompressed), keyword 'chara' for the V1/V2 card.
- The 'chara' chunk is inserted immediately before IEND.

Reads tEXt and zTXt, keyword 'chara' (and 'ccv3' for V3, which takes precedence).

References
----------
- Character Card V2 spec (PNG embedding):
  https://github.com/malfoyslastname/character-card-spec-v2
- SillyTavern card import/export:
  https://github.com/SillyTavern/SillyTavern/blob/release/src/character-card-parser.js
"""
from __future__ import annotations

import base64
import struct
import zlib
from dataclasses import dataclass
from typing import Optional

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# SillyTavern reads V3 ('ccv3') first, then V2 ('chara').
KEYWORD_PRECEDENCE = ("ccv3", "chara")


@dataclass(frozen=True)
class Chunk:
    type: bytes
    data: bytes


class PngError(ValueError):
    """Raised when the input is not a decodable PNG or the payload is invalid."""


# ---------------------------------------------------------------- chunk I/O
def read_chunks(blob: bytes, verify_crc: bool = True) -> list[Chunk]:
    if blob[:8] != PNG_SIGNATURE:
        raise PngError("not a PNG file (bad signature)")
    chunks: list[Chunk] = []
    pos = 8
    while pos < len(blob):
        if pos + 8 > len(blob):
            raise PngError("truncated chunk header")
        (length,) = struct.unpack(">I", blob[pos : pos + 4])
        ctype = blob[pos + 4 : pos + 8]
        data_start = pos + 8
        data_end = data_start + length
        if data_end + 4 > len(blob):
            raise PngError(f"chunk {ctype!r} declared length overruns the file")
        data = blob[data_start:data_end]
        (crc,) = struct.unpack(">I", blob[data_end : data_end + 4])
        if verify_crc and (zlib.crc32(ctype + data) & 0xFFFFFFFF) != crc:
            raise PngError(f"chunk {ctype!r} has invalid CRC")
        chunks.append(Chunk(ctype, data))
        pos = data_end + 4  # skip CRC
        if ctype == b"IEND":
            break
    else:
        raise PngError("missing IEND chunk")
    types = {c.type for c in chunks}
    if b"IHDR" not in types:
        raise PngError("missing IHDR chunk")
    if b"IDAT" not in types:
        raise PngError("missing IDAT chunk (no image data)")
    return chunks


def write_chunks(chunks: list[Chunk]) -> bytes:
    out = bytearray(PNG_SIGNATURE)
    for c in chunks:
        out += struct.pack(">I", len(c.data))
        out += c.type
        out += c.data
        out += struct.pack(">I", zlib.crc32(c.type + c.data) & 0xFFFFFFFF)
    return bytes(out)


# ---------------------------------------------------------------- tEXt/zTXt
def _decode_text_payload(ctype: bytes, data: bytes) -> tuple[bytes, bytes]:
    if ctype == b"tEXt":
        if b"\x00" not in data:
            raise PngError("tEXt chunk missing null separator")
        kw, payload = data.split(b"\x00", 1)
        return kw, payload
    if ctype == b"zTXt":
        if b"\x00" not in data:
            raise PngError("zTXt chunk missing null separator")
        kw, rest = data.split(b"\x00", 1)
        if not rest:
            raise PngError("zTXt chunk missing compression method")
        if rest[0] != 0:
            raise PngError("unsupported zTXt compression method (expected zlib)")
        try:
            payload = zlib.decompress(rest[1:])
        except zlib.error as exc:
            raise PngError(f"zTXt decompression failed: {exc}") from exc
        return kw, payload
    raise PngError(f"unsupported chunk type {ctype!r}")


def read_card_payloads(blob: bytes) -> dict[bytes, str]:
    """Return {keyword: text} for every tEXt/zTXt text chunk in the PNG."""
    out: dict[bytes, str] = {}
    for ch in read_chunks(blob):
        if ch.type in (b"tEXt", b"zTXt"):
            kw, payload = _decode_text_payload(ch.type, ch.data)
            out[kw] = payload.decode("utf-8", errors="replace")
    return out


# ------------------------------------------------------------- card JSON API
def _payload_to_json(text: str) -> dict:
    """Decode one tEXt/zTXt payload into a JSON object.

    SillyTavern base64-encodes the JSON; raw JSON is also tolerated on read.
    """
    import json

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            decoded = base64.b64decode(text, validate=True).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise PngError(f"payload is neither JSON nor base64 JSON: {exc}") from exc
        try:
            return json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise PngError(f"decoded payload is not valid JSON: {exc}") from exc


def parse_png_card(blob: bytes) -> dict:
    """Extract and parse the character card embedded in a PNG.

    'ccv3' takes precedence over 'chara' (matches SillyTavern's read order).
    """
    if len(blob) > 10 * 1024 * 1024:
        raise PngError("PNG exceeds the 10 MB safety limit")
    payloads = read_card_payloads(blob)
    for kw in KEYWORD_PRECEDENCE:
        kw_bytes = kw.encode("utf-8")
        if kw_bytes in payloads:
            card = _payload_to_json(payloads[kw_bytes])
            if not isinstance(card, dict):
                raise PngError(f"'{kw}' payload is not a JSON object")
            return card
    raise PngError(
        "no character data found in PNG (looked for tEXt/zTXt chunks 'ccv3'/'chara')"
    )


def _make_textr_chunk(keyword: bytes, text: str) -> Chunk:
    data = keyword + b"\x00" + text.encode("utf-8")
    return Chunk(b"tEXt", data)


def embed_card_png(card_json: dict, template_png: Optional[bytes] = None) -> bytes:
    """Embed a V2 card as a base64 JSON in a 'chara' tEXt chunk.

    If `template_png` is provided, the card is written into that image,
    replacing any existing 'chara'/'ccv3' chunks (no duplicate payloads are
    left behind). Otherwise a 1x1 transparent placeholder PNG is used.

    Existing card keywords are matched case-insensitively (SillyTavern's
    reader is case-insensitive; an uppercase 'CCV3' left behind would
    override the new 'chara' chunk on import).
    """
    import json

    if template_png is not None and template_png[:8] != PNG_SIGNATURE:
        raise PngError("template is not a PNG file (bad signature)")
    if template_png is None:
        template_png = _placeholder_png()

    chunks = read_chunks(template_png, verify_crc=False)
    # Strip competing card payloads so the output carries exactly one card.
    # Match case-insensitively because SillyTavern's reader does too.
    card_keywords = {b"chara", b"ccv3"}
    cleaned = []
    for c in chunks:
        if c.type in (b"tEXt", b"zTXt"):
            kw = _peek_keyword(c)
            if kw.lower() in card_keywords:
                continue
        cleaned.append(c)
    encoded = base64.b64encode(
        json.dumps(card_json, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    chara = _make_textr_chunk(b"chara", encoded)
    # Insert immediately before IEND, per SillyTavern's write layout.
    iend = next(i for i, c in enumerate(cleaned) if c.type == b"IEND")
    cleaned.insert(iend, chara)
    return write_chunks(cleaned)


def _peek_keyword(ch: Chunk) -> bytes:
    return ch.data.split(b"\x00", 1)[0] if b"\x00" in ch.data else b""


def _placeholder_png() -> bytes:
    """A minimal valid 1x1 transparent RGBA PNG.

    Scanline = 1 filter byte + 4 channel bytes = 5 bytes total.
    """
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00\x00")
    out = bytearray(PNG_SIGNATURE)
    for t, d in ((b"IHDR", ihdr), (b"IDAT", idat), (b"IEND", b"")):
        out += struct.pack(">I", len(d)) + t + d
        out += struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return bytes(out)
