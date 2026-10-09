"""Validation of generated cards against SillyTavern's import contracts.

Three layers:
1. JSON structural checks (required V2 fields, types, first_mes mapping).
2. Lorebook checks (both standalone World Info JSON and embedded character_book).
3. PNG checks (signature, tEXt 'chara' payload decodes, round-trip identity).

References
----------
- V2 spec required fields:
  https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md
- SillyTavern import requirements:
  https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/characters.js
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from png_metadata import PngError, PNG_SIGNATURE, parse_png_card, read_card_payloads, read_chunks

REQUIRED_V2_FIELDS = [
    "name", "description", "personality", "scenario",
    "first_mes", "mes_example", "creator_notes", "system_prompt",
    "post_history_instructions", "alternate_greetings", "tags",
    "creator", "character_version", "extensions",
]
REQUIRED_BOOK_ENTRY_FIELDS = [
    "keys", "content", "extensions", "enabled", "insertion_order",
]
# position is optional in the V2 spec; if present, must be one of these.
POSITION_VALUES = {"before_char", "after_char"}
ROLE_VALUES = {"system", "user", "assistant"}


@dataclass
class Report:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def __str__(self) -> str:
        lines = ["VALID" if self.ok else "INVALID"]
        for e in self.errors:
            lines.append(f"  error:   {e}")
        for w in self.warnings:
            lines.append(f"  warning: {w}")
        return "\n".join(lines)


def validate_card_json(card: object) -> Report:
    """Validate a V2 card dict (either authoring output or re-parsed PNG payload).

    Never raises: malformed input yields an invalid Report.
    """
    r = Report()
    if not isinstance(card, dict):
        r.error(f"card root must be a JSON object, got {type(card).__name__}")
        return r
    if card.get("spec") != "chara_card_v2":
        r.error(f"spec must be 'chara_card_v2', got {card.get('spec')!r}")
    spec_version = card.get("spec_version")
    if not isinstance(spec_version, str) or spec_version != "2.0":
        r.error(f"spec_version must be the string '2.0', got {spec_version!r}")
    data = card.get("data")
    if not isinstance(data, dict):
        r.error("missing or non-object 'data' wrapper")
        return r
    for f in REQUIRED_V2_FIELDS:
        if f not in data:
            r.error(f"data missing required field '{f}'")
    if not isinstance(data.get("name"), str) or not data.get("name", "").strip():
        r.error("data.name must be a non-empty string")
    for f in ("description", "personality", "scenario", "first_mes", "mes_example",
              "creator_notes", "system_prompt", "post_history_instructions"):
        if f in data and not isinstance(data[f], str):
            r.error(f"data.{f} must be a string, got {type(data[f]).__name__}")
    for f in ("creator", "character_version"):
        if f in data and not isinstance(data[f], str):
            r.error(f"data.{f} must be a string, got {type(data[f]).__name__}")
    for f in ("alternate_greetings", "tags"):
        if f in data:
            if not isinstance(data[f], list):
                r.error(f"data.{f} must be an array")
            else:
                for i, el in enumerate(data[f]):
                    if not isinstance(el, str):
                        r.error(f"data.{f}[{i}] must be a string")
    ext = data.get("extensions")
    if "extensions" in data and not isinstance(ext, dict):
        r.error(f"data.extensions must be an object, got {type(ext).__name__ if ext is not None else 'None'}")
        ext = None
    # Legacy authoring names must not leak into the spec payload.
    for f in ("first_message", "character_note", "lorebook"):
        if f in data:
            r.error(f"data.{f} is an authoring-only name; use the spec name instead")
    # depth_prompt (Character's Note) shape — role is a STRING per ST source.
    dp = (ext or {}).get("depth_prompt")
    if dp is not None:
        if not isinstance(dp, dict):
            r.error("extensions.depth_prompt must be an object")
        else:
            if not isinstance(dp.get("prompt", ""), str):
                r.error("extensions.depth_prompt.prompt must be a string")
            role_val = dp.get("role", "system")
            if not isinstance(role_val, str):
                r.error(
                    f"extensions.depth_prompt.role must be a string in "
                    f"{sorted(ROLE_VALUES)}, got {type(role_val).__name__}"
                )
            elif role_val not in ROLE_VALUES:
                r.error(
                    "extensions.depth_prompt.role must be one of "
                    f"{sorted(ROLE_VALUES)}, got {role_val!r}"
                )
            if not isinstance(dp.get("depth", 4), int) or isinstance(dp.get("depth", 4), bool) or dp.get("depth", 4) < 0:
                r.error("extensions.depth_prompt.depth must be a non-negative integer")
    book = data.get("character_book")
    if book is not None:
        if not isinstance(book, dict):
            r.error("data.character_book must be an object")
        else:
            _validate_embedded_book(book, r)
    return r


def _validate_embedded_book(book: dict, r: Report) -> None:
    if not isinstance(book, dict):
        r.error("data.character_book must be an object")
        return
    entries = book.get("entries")
    if not isinstance(entries, list):
        r.error("character_book.entries must be an array (V2 shape)")
        return
    if "extensions" not in book:
        r.error("character_book.extensions is required (may be an empty object)")
    elif not isinstance(book["extensions"], dict):
        r.error("character_book.extensions must be an object")
    for f in ("name", "description"):
        if f in book and not isinstance(book[f], str):
            r.error(f"character_book.{f} must be a string")
    for f in ("scan_depth", "token_budget"):
        if f in book:
            v = book[f]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                r.error(f"character_book.{f} must be a number")
    if "recursive_scanning" in book and not isinstance(book["recursive_scanning"], bool):
        r.error("character_book.recursive_scanning must be boolean")
    seen_ids: set[int] = set()
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            r.error(f"character_book.entries[{i}] must be an object")
            continue
        for f in REQUIRED_BOOK_ENTRY_FIELDS:
            if f not in e:
                r.error(f"character_book.entries[{i}] missing required field '{f}'")
        if not isinstance(e.get("keys"), list):
            r.error(f"character_book.entries[{i}].keys must be an array")
        else:
            for j, k in enumerate(e["keys"]):
                if not isinstance(k, str):
                    r.error(f"character_book.entries[{i}].keys[{j}] must be a string")
        if not e.get("keys") and not e.get("constant"):
            # V2 permits empty keys; a non-constant entry with no keys never fires.
            r.warn(
                f"character_book.entries[{i}] has no keys and is not constant; "
                "it will never activate"
            )
        if not isinstance(e.get("content"), str):
            r.error(f"character_book.entries[{i}].content must be a string")
        if not isinstance(e.get("enabled"), bool):
            r.error(f"character_book.entries[{i}].enabled must be boolean")
        for f in ("case_sensitive", "selective", "constant"):
            if f in e and not isinstance(e[f], bool):
                r.error(f"character_book.entries[{i}].{f} must be boolean")
        for f in ("name", "comment"):
            if f in e and not isinstance(e[f], str):
                r.error(f"character_book.entries[{i}].{f} must be a string")
        if "extensions" in e:
            if not isinstance(e["extensions"], dict):
                r.error(f"character_book.entries[{i}].extensions must be an object")
        if "position" in e:
            pos_val = e["position"]
            if not isinstance(pos_val, str):
                r.error(
                    f"character_book.entries[{i}].position must be a string in "
                    f"{sorted(POSITION_VALUES)}, got {type(pos_val).__name__}"
                )
            elif pos_val not in POSITION_VALUES:
                r.error(
                    f"character_book.entries[{i}].position must be one of "
                    f"{sorted(POSITION_VALUES)}, got {pos_val!r}"
                )
        if "insertion_order" in e:
            io_val = e["insertion_order"]
            if isinstance(io_val, bool) or not isinstance(io_val, (int, float)):
                r.error(f"character_book.entries[{i}].insertion_order must be a number (not bool)")
        if "priority" in e:
            pr_val = e["priority"]
            if isinstance(pr_val, bool) or not isinstance(pr_val, (int, float)):
                r.error(f"character_book.entries[{i}].priority must be a number (not bool)")
        if "secondary_keys" in e:
            if not isinstance(e["secondary_keys"], list):
                r.error(f"character_book.entries[{i}].secondary_keys must be an array")
            else:
                for j, k in enumerate(e["secondary_keys"]):
                    if not isinstance(k, str):
                        r.error(f"character_book.entries[{i}].secondary_keys[{j}] must be a string")
        uid = e.get("id")
        if uid is not None:
            if not isinstance(uid, int) or isinstance(uid, bool):
                r.error(f"character_book.entries[{i}].id must be an integer")
            elif uid in seen_ids:
                r.error(f"character_book.entries[{i}].id {uid} is duplicated")
            else:
                seen_ids.add(uid)


def validate_world_info_json(wi: object) -> Report:
    """Validate a standalone SillyTavern World Info JSON. Never raises."""
    r = Report()
    if not isinstance(wi, dict):
        r.error(f"World Info root must be an object, got {type(wi).__name__}")
        return r
    for f in ("name", "description"):
        if f in wi and not isinstance(wi[f], str):
            r.error(f"{f} must be a string")
    for f in ("scanDepth", "tokenBudget"):
        if f in wi:
            v = wi[f]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                r.error(f"{f} must be a number")
    if "recursiveScanning" in wi and not isinstance(wi["recursiveScanning"], bool):
        r.error("recursiveScanning must be boolean")
    if "extensions" in wi and not isinstance(wi["extensions"], dict):
        r.error("extensions must be an object")
    entries = wi.get("entries")
    if not isinstance(entries, dict):
        r.error("top-level 'entries' must be an object (keyed by stringified uid)")
        return r
    seen_uids: set[int] = set()
    for key, e in entries.items():
        if not isinstance(e, dict):
            r.error(f"entries[{key!r}] must be an object")
            continue
        # Entry-level scanDepth: null means "inherit the global setting";
        # booleans are invalid.
        if "scanDepth" in e:
            sd = e["scanDepth"]
            if sd is not None and (isinstance(sd, bool) or not isinstance(sd, (int, float))):
                r.error(f"entries[{key}].scanDepth must be a number or null")
        if "key" not in e:
            r.error(f"entries[{key}].key is required")
        for f in ("key", "keysecondary"):
            if f in e:
                if not isinstance(e[f], list):
                    r.error(f"entries[{key}].{f} must be an array")
                else:
                    for j, k in enumerate(e[f]):
                        if not isinstance(k, str):
                            r.error(f"entries[{key}].{f}[{j}] must be a string")
        if "uid" not in e or e.get("uid") is None:
            r.error(f"entries[{key}].uid is required")
        if not key.isdigit():
            r.error(f"entries key {key!r} must be a stringified integer uid")
        if not isinstance(e.get("content"), str):
            r.error(f"entries[{key}].content must be a string")
        if not isinstance(e.get("constant"), bool):
            r.error(f"entries[{key}].constant must be boolean")
        uid = e.get("uid")
        if uid is None:
            pass  # already reported above
        elif not isinstance(uid, int) or isinstance(uid, bool):
            r.error(f"entries[{key}].uid must be an integer")
        elif uid in seen_uids:
            r.error(f"entries[{key}].uid {uid} is duplicated")
        else:
            seen_uids.add(uid)
    return r


def validate_png_card(blob: bytes) -> Report:
    """Validate a PNG against SillyTavern's actual import contract.

    Uses the ST-faithful importer (tEXt-only, case-insensitive keyword,
    ccv3-before-chara, first-match-wins, strict base64) plus a Pillow pixel
    decode (`Image.load()`). Never raises.
    """
    from st_import import STImportError, decode_pixels_with_pillow, parse_card_st_strict

    r = Report()
    if len(blob) > 10 * 1024 * 1024:
        r.error("PNG exceeds the 10 MB safety limit")
        return r
    if blob[:8] != PNG_SIGNATURE:
        r.error("not a PNG file (bad signature)")
        return r
    try:
        read_chunks(blob)
    except PngError as exc:
        r.error(f"PNG structure invalid: {exc}")
        return r
    try:
        card = parse_card_st_strict(blob)
    except STImportError as exc:
        r.error(f"ST import would fail: {exc}")
        return r
    try:
        decode_pixels_with_pillow(blob)
    except STImportError as exc:
        r.error(f"image pixels do not decode: {exc}")
        return r
    inner = validate_card_json(card)
    if not inner.ok:
        r.errors.extend(inner.errors)
        r.ok = False
    r.warnings.extend(inner.warnings)
    return r


def validate_png_roundtrip(card_dict: dict, png_blob: bytes) -> Report:
    """Confirm the PNG round-trips: embedded payload equals the source dict.

    Uses the permissive extractor (round-trip is a tooling invariant, not an
    ST-import claim)."""
    r = Report()
    try:
        parsed = parse_png_card(png_blob)
    except (PngError, ValueError) as exc:
        r.error(f"round-trip parse failed: {exc}")
        return r
    if parsed != card_dict:
        r.error("round-trip mismatch: PNG payload differs from source JSON")
    return r
