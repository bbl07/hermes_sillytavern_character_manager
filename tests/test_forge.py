#!/usr/bin/env python3
"""End-to-end tests for hermes-st-card-forge. Run: python3 tests/test_forge.py

Positive + negative coverage per advisor review:
- required V2 fields/types, Unicode, authoring-name leakage (first_message vs first_mes)
- both lorebook shapes (standalone World Info + embedded character_book)
- ST macro ({{char}}/{{user}}) preservation through prompt rendering
- undefined `<<var>>` rejection
- malformed PNG, invalid base64 payload, oversized file
- JSON -> PNG -> JSON round-trip identity
- embedded PNG replacement (no duplicate chara/ccv3 payloads)
- user-supplied template PNG becomes the primary image unchanged (pixel-identical)

Spec-checked, not import-tested against a live SillyTavern build.
"""
from __future__ import annotations

import base64
import json
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from card_schema import CharacterCardV2, DepthPrompt, CharacterBook, LorebookEntry, from_template
from lorebook_generator import LorebookSource, standalone_world_info, to_character_book
from png_metadata import PngError, embed_card_png, parse_png_card, read_chunks
from prompt_parser import render_prompt, substitute_variables
from packager import package_card
from validate_cards import (
    validate_card_json,
    validate_png_card,
    validate_png_roundtrip,
    validate_world_info_json,
)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append((name, detail))
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f" :: {detail}" if detail and not cond else ""))


def make_png(w=8, h=8, rgb=(120, 90, 200)) -> bytes:
    px = bytes(rgb) * w
    raw = b"".join(b"\x00" + px for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


# --------------------------------------------------------------- fixtures
tmpl = json.loads((ROOT / "templates" / "card_v2_template.json").read_text())
user_png = (ROOT / "assets" / "examples" / "portrait_example.png").read_bytes()
env_png = (ROOT / "assets" / "examples" / "environment_example.png").read_bytes()

card = CharacterCardV2(
    name="Yumi",
    description="A tea-shop owner in a rainy coastal city.",
    personality="Warm, dry humor.",
    scenario="{{user}} shelters from a storm.",
    first_message="*The door chimes as rain follows {{user}} inside.*\n\n\"Sit. I'll bring the oolong.\"",
    mes_example="<START>\n{{user}}: Hello\n{{char}}: *She raises an eyebrow.* \"You're new.\"",
    alternate_greetings=["*Another rainy evening at the shop.*"],
    tags=["slice_of_life"],
    creator="Hermes",
    character_version="1.0",
    character_book=CharacterBook(
        name="Yumi's World",
        entries=[
            LorebookEntry(keys=["shop", "teahouse"], content="A narrow storefront with a worn oak counter."),
            LorebookEntry(keys=["city", "harbor"], content="A rain-washed harbor town.", constant=True),
        ],
    ),
    character_note=DepthPrompt(prompt="Yumi never uses honorifics with regulars."),
)
card_dict = card.to_dict()

# ------------------------------------------------------------- spec checks
r = validate_card_json(card_dict)
check("valid V2 card passes", r.ok, str(r.errors))
check("spec field is chara_card_v2", card_dict["spec"] == "chara_card_v2")
check("first_message maps to first_mes", "first_mes" in card_dict["data"])
check("character_note maps to depth_prompt", "depth_prompt" in card_dict["data"]["extensions"])
check("character_book is under data", "character_book" in card_dict["data"])

# Negative: authoring names leaked into spec output.
bad = json.loads(json.dumps(card_dict))
bad["data"]["first_message"] = "leak"
bad["data"]["character_note"] = "leak"
r = validate_card_json(bad)
check("authoring names rejected in spec payload", not r.ok and any("authoring-only" in e for e in r.errors))

# Negative: missing required field.
bad2 = json.loads(json.dumps(card_dict))
del bad2["data"]["tags"]
r = validate_card_json(bad2)
check("missing required field caught", not r.ok and any("tags" in e for e in r.errors))

# Unicode preservation.
uni_card = json.loads(json.dumps(card_dict))
uni_card["data"]["name"] = "Yumi—歩夢 🍵"
r = validate_card_json(uni_card)
check("Unicode name preserved and valid", r.ok and uni_card["data"]["name"] == "Yumi—歩夢 🍵")

# --------------------------------------------------------- lorebook shapes
sources = [
    LorebookSource(title="Shop", keys=["shop", "teahouse"], content="A narrow storefront."),
    LorebookSource(title="City", keys=["city", "harbor"], content="A rain-washed harbor town.",
                   constant=True, order=200),
]
wi = standalone_world_info("Yumi's World", sources, description="Setting lore.")
r = validate_world_info_json(wi)
check("standalone World Info JSON valid", r.ok, str(r.errors))
check("standalone uses entries-by-uid dict", isinstance(wi["entries"], dict) and "0" in wi["entries"])
check("standalone entry has uid+key+content", all(
    k in wi["entries"]["0"] for k in ("uid", "key", "content")))

book = to_character_book("Yumi's World", sources)
check("embedded character_book uses entries array", isinstance(book.to_dict()["entries"], list))
check("embedded book ids are sequential", [e["id"] for e in book.to_dict()["entries"]] == [0, 1])

# Duplicate uid must be rejected.
wi_bad = json.loads(json.dumps(wi))
wi_bad["entries"]["1"]["uid"] = 0
r = validate_world_info_json(wi_bad)
check("duplicate World Info uid caught", not r.ok and any("duplicated" in e for e in r.errors))

# ---------------------------------------------------------- prompt parsing
rendered = render_prompt(
    {"first_message": "{{char}} greets {{user}} with <<tone_word>> warmth.",
     "description": "", "personality": "", "scenario": "",
     "mes_example": "", "system_prompt": "", "character_note": ""},
    variables={"tone_word": "quiet"},
    tone_preset="literary",
)
check("authoring var substituted", rendered.first_message == "{{char}} greets {{user}} with quiet warmth.")
check("ST macros preserved", "{{char}}" in rendered.first_message and "{{user}}" in rendered.first_message)
check("tone preset appended to system_prompt", "literary third-person" in rendered.system_prompt)
check("macro audit lists char+user", set(rendered.macros_found) >= {"char", "user"})

try:
    substitute_variables("Hello <<undefined_name>>", {})
    check("undefined variable rejected", False, "no exception raised")
except KeyError:
    check("undefined variable rejected", True)

# --------------------------------------------------------------- PNG layer
png_blob = embed_card_png(card_dict, template_png=user_png)
r = validate_png_card(png_blob)
check("embedded PNG validates against import spec", r.ok, str(r.errors))
rt = validate_png_roundtrip(card_dict, png_blob)
check("JSON -> PNG -> JSON round-trip identical", rt.ok, str(rt.errors))
parsed = parse_png_card(png_blob)
check("round-trip preserves {{user}} macros", "{{user}}" in parsed["data"]["first_mes"])
check("round-trip preserves character_book", parsed["data"]["character_book"]["name"] == "Yumi's World")
check("round-trip preserves depth_prompt", parsed["data"]["extensions"]["depth_prompt"]["prompt"].startswith("Yumi never"))

# User PNG pixels unchanged: IDAT chunks identical before/after embed.
before_idat = [c.data for c in read_chunks(user_png) if c.type == b"IDAT"]
after_idat = [c.data for c in read_chunks(png_blob) if c.type == b"IDAT"]
check("user-supplied PNG pixels unchanged", before_idat == after_idat)

# Replacing an existing card's payload leaves exactly one chara chunk.
re_embed = embed_card_png(card_dict, template_png=png_blob)
chara_count = sum(1 for c in read_chunks(re_embed) if c.type == b"tEXt" and c.data.startswith(b"chara\x00"))
check("re-embed leaves exactly one chara chunk", chara_count == 1, f"got {chara_count}")

# Negative: plain PNG with no card.
plain = make_png()
r = validate_png_card(plain)
check("plain PNG rejected (no card metadata)",
      not r.ok and any("tEXt" in e or "chara" in e for e in r.errors),
      str(r.errors))

# Negative: malformed PNG signature.
r = validate_png_card(b"\x00" * 64)
check("malformed signature rejected", not r.ok)

# Negative: invalid base64 in chara chunk.
bogus = embed_card_png(card_dict, template_png=plain)
chunks = read_chunks(bogus)
for i, c in enumerate(chunks):
    if c.type == b"tEXt" and c.data.startswith(b"chara\x00"):
        chunks[i] = type(c)(b"tEXt", b"chara\x00" + b"!!!not-base64!!!")
import png_metadata as pm
rebuilt = pm.write_chunks(chunks)
r = validate_png_card(rebuilt)
check("invalid base64 payload rejected", not r.ok)

# Negative: 10MB limit enforced.
r = validate_png_card(b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024 + 16))
check("oversized PNG rejected", not r.ok and any("10 MB" in e for e in r.errors))

# -------------------------------------------------------------- packaging
import shutil, tempfile

tmp = Path(tempfile.mkdtemp())
manifest = package_card(card_dict, tmp / "dist", template_png_path=ROOT / "assets/examples/portrait_example.png",
                        asset_paths=[ROOT / "assets/examples/environment_example.png"],
                        slug="yumi")
check("package wrote json+png", (manifest["json"]).exists() and (manifest["png"]).exists())
check("package bundle exists", manifest["assets_dir"] is not None and manifest["assets_dir"].exists())
check("package round-trip verified", manifest["roundtrip_ok"])
check("environment PNG copied verbatim", (manifest["assets_dir"] / "environment_example.png").read_bytes() == env_png)
r = validate_png_card(manifest["png"].read_bytes())
check("packaged PNG validates", r.ok, str(r.errors))

shutil.rmtree(tmp)

# ------------------------------------------------------------------ report
print("\n" + "=" * 72)
print(f"passed={len(PASS)}  failed={len(FAIL)}")
for name, detail in FAIL:
    print(f"  FAIL {name} :: {detail}")
sys.exit(1 if FAIL else 0)
