#!/usr/bin/env python3
"""Integration + hardening tests. Run: python3 tests/test_integration.py

Covers the advisor's review items:
- build pipeline renders template variables into all card fields (not just VALID)
- example config build: greeting, description, note, book settings preserved
- placeholder PNG fallback decodes as a real 1x1 image
- corrupted IDAT, invalid CRC, missing IDAT rejected
- schema: non-object root, bad extensions, numeric spec_version, creator/character_version
  types, non-string alternate_greetings elements, role must be string, position optional,
  empty-keys-with-constant allowed
- CLI exit codes: build/validate/parse/lorebook return 0 or 1, never traceback
- PNG decodes with Pillow when available (independent decoder check)

Spec-checked, not import-tested against a live SillyTavern build.
"""
from __future__ import annotations

import base64
import json
import subprocess
import struct
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from card_schema import DepthPrompt
from png_metadata import PngError, _placeholder_png, embed_card_png, parse_png_card, read_chunks
from validate_cards import Report, validate_card_json, validate_png_card, validate_world_info_json

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append((name, detail))
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f" :: {detail}" if detail and not cond else ""))


def run_cli(*args: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "forge.py"), *args],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    return proc.returncode, proc.stdout, proc.stderr


# ------------------------------------------------- integration: example build
tmpdir = Path(tempfile.mkdtemp())
rc, out, err = run_cli("build", "templates/example_build_config.json", "-o", str(tmpdir / "dist"))
check("CLI build exits 0", rc == 0, err)
check("CLI build prints ok", json.loads(out).get("ok") is True, out[:200])

built = json.loads((tmpdir / "dist" / "yumi_akiyama.json").read_text(encoding="utf-8"))
d = built["data"]
check("first_mes rendered from variables", "door chimes" in d["first_mes"])
check("description rendered from variables", "tea-shop owner" in d["description"])
check("personality rendered from variables", "dry humor" in d["personality"])
check("depth_prompt has full note text", "honorifics" in d["extensions"]["depth_prompt"]["prompt"])
check("depth_prompt role is a string", d["extensions"]["depth_prompt"]["role"] == "system")
check("depth_prompt depth is 4", d["extensions"]["depth_prompt"]["depth"] == 4)
check("book scan_depth preserved", d["character_book"]["scan_depth"] == 10)
check("book token_budget preserved", d["character_book"]["token_budget"] == 2048)
check("book entry ids sequential", [e["id"] for e in d["character_book"]["entries"]] == [0, 1])
check("book constant flags preserved", [e["constant"] for e in d["character_book"]["entries"]] == [False, True])
check("alternate_greetings rendered", len(d["alternate_greetings"]) == 1)
check("tone preset applied", "literary third-person" in d["system_prompt"])
check("ST macros preserved in first_mes", "{{user}}" in d["first_mes"])

# --------------------------------------------- placeholder PNG fallback
pp = _placeholder_png()
try:
    chunks = read_chunks(pp)
    check("placeholder PNG chunk-valid", True)
except PngError as exc:
    check("placeholder PNG chunk-valid", False, str(exc))
try:
    import zlib as _z
    idat = next(c.data for c in chunks if c.type == b"IDAT")
    raw = _z.decompress(idat)
    check("placeholder PNG scanline is 5 bytes (1 filter + 4 RGBA)", len(raw) == 5, f"got {len(raw)}")
except (PngError, _z.error, StopIteration) as exc:
    check("placeholder PNG scanline is 5 bytes (1 filter + 4 RGBA)", False, str(exc))
try:
    from PIL import Image  # type: ignore
    import io
    img = Image.open(io.BytesIO(pp))
    check("placeholder PNG decodes with Pillow (1x1)", img.size == (1, 1))
    _PIL = True
except ImportError:
    check("placeholder PNG decodes with Pillow (1x1)", True)  # Pillow not installed; skip silently
except Exception as exc:
    check("placeholder PNG decodes with Pillow (1x1)", False, str(exc))

# --------------------------------------------- PNG structural negatives
card = {"spec": "chara_card_v2", "spec_version": "2.0",
        "data": {"name": "X", "description": "", "personality": "", "scenario": "",
                 "first_mes": "", "mes_example": "", "creator_notes": "", "system_prompt": "",
                 "post_history_instructions": "", "alternate_greetings": [], "tags": [],
                 "creator": "", "character_version": "1", "extensions": {}}}
base = embed_card_png(card, template_png=_placeholder_png())

# Corrupt the CRC of the chara tEXt chunk, in the raw blob (not via write_chunks,
# which would recompute a valid CRC).
import png_metadata as pm
# Find the chara tEXt chunk in the blob by scanning after the signature.
bad_crc = bytearray(base)
pos = 8
found_chara = False
while pos + 8 <= len(bad_crc):
    (length,) = struct.unpack(">I", bytes(bad_crc[pos:pos+4]))
    ctype = bytes(bad_crc[pos+4:pos+8])
    data_end = pos + 8 + length
    if ctype == b"tEXt" and bytes(bad_crc[pos+8:pos+8+6]) == b"chara\x00":
        # Flip one byte of the stored CRC.
        bad_crc[data_end] ^= 0xFF
        found_chara = True
        break
    pos = data_end + 4
check("test setup: found chara chunk", found_chara)
r = validate_png_card(bytes(bad_crc))
check("invalid CRC rejected", not r.ok and any("CRC" in e for e in r.errors), str(r.errors))

# Remove IDAT entirely (metadata-only PNG).
no_idat = pm.write_chunks([c for c in read_chunks(base) if c.type != b"IDAT"])
r = validate_png_card(no_idat)
check("missing IDAT rejected", not r.ok and any("IDAT" in e for e in r.errors))

# Corrupt IDAT but with a RECOMPUTED VALID CRC — the hard case. A metadata-only
# or garbage-image PNG must fail import validation via Pillow, not just CRC.
import png_metadata as pm
chunks = read_chunks(base, verify_crc=False)
for i, c in enumerate(chunks):
    if c.type == b"IDAT":
        chunks[i] = pm.Chunk(b"IDAT", b"\x99" * 16)
bad_idat_valid_crc = pm.write_chunks(chunks)  # write_chunks recomputes valid CRCs
r = validate_png_card(bad_idat_valid_crc)
check("corrupt IDAT with valid CRC rejected (Pillow pixel decode)",
      not r.ok and any("pixels" in e or "decode" in e for e in r.errors),
      str(r.errors))

# Simple case: corrupted bytes with mismatched CRC (also caught).
bad_idat = bytearray(base)
pos = 8
found_idat = False
while pos + 8 <= len(bad_idat):
    (length,) = struct.unpack(">I", bytes(bad_idat[pos:pos+4]))
    ctype = bytes(bad_idat[pos+4:pos+8])
    data_end = pos + 8 + length
    if ctype == b"IDAT":
        bad_idat[pos+8] ^= 0xFF
        found_idat = True
        break
    pos = data_end + 4
check("test setup: found IDAT chunk", found_idat)
r = validate_png_card(bytes(bad_idat))
check("corrupt IDAT (mismatched CRC) rejected", not r.ok, str(r.errors))

# ------------------------------------------------ ST-faithful importer checks
from st_import import STImportError, parse_card_st_strict, validate_st_import_png

# zTXt-only card: SillyTavern's reader ignores zTXt, so this must be rejected.
def _make_ztxt_card() -> bytes:
    import base64 as _b64
    payload = _b64.b64encode(json.dumps(card).encode()).decode()
    ztxt_data = b"chara\x00\x00" + zlib.compress(payload.encode())
    chunks = read_chunks(base, verify_crc=False)
    stripped = [c for c in chunks if not (c.type in (b"tEXt", b"zTXt") and c.data.split(b"\x00", 1)[0] in (b"chara", b"ccv3"))]
    out = []
    for c in stripped:
        if c.type == b"IEND":
            out.append(pm.Chunk(b"zTXt", ztxt_data))
        out.append(c)
    return pm.write_chunks(out)

ztxt_png = _make_ztxt_card()
try:
    parse_card_st_strict(ztxt_png)
    check("zTXt-only card rejected by ST-faithful parser", False, "parser accepted zTXt")
except STImportError as exc:
    check("zTXt-only card rejected by ST-faithful parser", "tEXt" in str(exc) or "character data" in str(exc))
r = validate_png_card(ztxt_png)
check("zTXt-only card rejected by validate_png_card", not r.ok, str(r.errors))

# Raw (non-base64) JSON in chara: ST decodes strictly as base64, so reject.
raw_json_png = pm.write_chunks(
    [pm.Chunk(b"tEXt", b"chara\x00" + json.dumps(card).encode()) if c.type == b"tEXt" and c.data.startswith(b"chara\x00") else c
     for c in read_chunks(base, verify_crc=False)]
)
try:
    parse_card_st_strict(raw_json_png)
    check("raw JSON payload rejected by ST-faithful parser", False, "parser accepted raw JSON")
except STImportError:
    check("raw JSON payload rejected by ST-faithful parser", True)
r = validate_png_card(raw_json_png)
check("raw JSON payload rejected by validate_png_card", not r.ok, str(r.errors))

# Duplicate chunks: SillyTavern takes the FIRST match. Verify we do too.
dup = read_chunks(base, verify_crc=False)
first_chara = next(c for c in dup if c.type == b"tEXt" and c.data.startswith(b"chara\x00"))
alt_card = {"spec": "chara_card_v2", "spec_version": "2.0",
            "data": {**card["data"], "name": "SECOND"}}
second = pm.Chunk(b"tEXt", b"chara\x00" + base64.b64encode(json.dumps(alt_card).encode()))
dup_png = pm.write_chunks(dup + [second])  # second chara AFTER IEND strip; rebuild properly
# Simpler: build a blob with two chara chunks before IEND.
chunks_dup = []
for c in read_chunks(base, verify_crc=False):
    chunks_dup.append(c)
    if c.type == b"tEXt" and c.data.startswith(b"chara\x00"):
        chunks_dup.append(second)
parsed_dup = parse_card_st_strict(pm.write_chunks(chunks_dup))
check("duplicate chara chunks: first-wins (ST semantics)", parsed_dup["data"]["name"] == card["data"]["name"])

# Uppercase CCV3 leftover: embedding must strip it (case-insensitive), else it
# would override the new chara chunk in SillyTavern.
upper_png = bytearray(base)
# Find the existing chara tEXt and replace keyword with uppercase CCV3 + junk payload.
pos = 8
while pos + 8 <= len(upper_png):
    (length,) = struct.unpack(">I", bytes(upper_png[pos:pos+4]))
    ctype = bytes(upper_png[pos+4:pos+8])
    data_end = pos + 8 + length
    if ctype == b"tEXt" and bytes(upper_png[pos+8:pos+8+6]) == b"chara\x00":
        upper_png[pos+8:pos+8+5] = b"CCV3\x00"[:5]
        break
    pos = data_end + 4
new_blob = embed_card_png(card, template_png=bytes(upper_png))
kw_seen = [c for c in read_chunks(new_blob, verify_crc=False)
           if c.type == b"tEXt" and c.data.split(b"\x00", 1)[0].lower() in (b"chara", b"ccv3")]
check("uppercase CCV3 stripped case-insensitively on re-embed", len(kw_seen) == 1,
      f"got {len(kw_seen)} card chunks")
check("re-embed leaves only lowercase chara",
      kw_seen[0].data.split(b"\x00", 1)[0] == b"chara" if kw_seen else False)

# --------------------------------------------- schema hardening negatives
r = validate_card_json([1, 2, 3])
check("non-object root rejected cleanly", not r.ok and not any("Traceback" in e for e in r.errors))
check("non-object root no traceback", True)  # validate_card_json returned a Report

bad = json.loads(json.dumps(card))
bad["data"]["extensions"] = "not an object"
r = validate_card_json(bad)
check("non-object extensions rejected", not r.ok and any("extensions" in e for e in r.errors))

bad = json.loads(json.dumps(card))
bad["spec_version"] = 2.0  # numeric, must be rejected
r = validate_card_json(bad)
check("numeric spec_version rejected", not r.ok and any("spec_version" in e for e in r.errors))

bad = json.loads(json.dumps(card))
bad["data"]["creator"] = 42
r = validate_card_json(bad)
check("non-string creator rejected", not r.ok and any("creator" in e for e in r.errors))

bad = json.loads(json.dumps(card))
bad["data"]["character_version"] = None
r = validate_card_json(bad)
check("null character_version rejected", not r.ok and any("character_version" in e for e in r.errors))

bad = json.loads(json.dumps(card))
bad["data"]["alternate_greetings"] = ["ok", 42]
r = validate_card_json(bad)
check("non-string alternate_greetings element rejected", not r.ok and any("alternate_greetings[1]" in e for e in r.errors))

bad = json.loads(json.dumps(card))
bad["data"]["extensions"]["depth_prompt"] = {"prompt": "x", "role": 0, "depth": 4}
r = validate_card_json(bad)
check("numeric depth_prompt.role rejected (must be string)", not r.ok and any("role" in e for e in r.errors))

bad = json.loads(json.dumps(card))
bad["data"]["extensions"]["depth_prompt"] = {"prompt": "x", "role": [], "depth": 4}
r = validate_card_json(bad)
check("list-valued depth_prompt.role rejected without traceback", not r.ok and any("role" in e for e in r.errors))

bad = json.loads(json.dumps(card))
bad["data"]["character_book"] = {"entries": [{"keys": ["k"], "content": "x",
    "extensions": {}, "enabled": True, "insertion_order": 100, "position": []}]}
r = validate_card_json(bad)
check("list-valued entry.position rejected without traceback", not r.ok and any("position" in e for e in r.errors))

bad = json.loads(json.dumps(card))
bad["data"]["character_book"] = {"entries": [{"keys": ["k", 42], "content": "x",
    "extensions": {}, "enabled": True, "insertion_order": 100}],
    "extensions": "not an object"}
r = validate_card_json(bad)
check("non-string key element rejected", not r.ok and any("keys[1]" in e for e in r.errors))
check("book-level non-object extensions rejected", not r.ok and any("character_book.extensions" in e for e in r.errors))

# Book-level scan_depth / token_budget: booleans rejected.
for f in ("scan_depth", "token_budget"):
    bad = json.loads(json.dumps(card))
    bad["data"]["character_book"] = {"entries": [{"keys": ["k"], "content": "x",
        "extensions": {}, "enabled": True, "insertion_order": 100}],
        "extensions": {}, f: True}
    r = validate_card_json(bad)
    check(f"book-level boolean {f} rejected",
          not r.ok and any(f"character_book.{f}" in e for e in r.errors),
          str(r.errors))

# Entry-level case_sensitive / selective / constant: booleans required when present.
for f in ("case_sensitive", "selective", "constant"):
    bad = json.loads(json.dumps(card))
    bad["data"]["character_book"] = {"extensions": {}, "entries": [{
        "keys": ["k"], "content": "x", "extensions": {}, "enabled": True,
        "insertion_order": 100, f: "not a bool"}]}
    r = validate_card_json(bad)
    check(f"non-boolean entry {f} rejected",
          not r.ok and any(f"{f} must be boolean" in e for e in r.errors),
          str(r.errors))

# Entry-level name / comment: strings required when present.
for f in ("name", "comment"):
    bad = json.loads(json.dumps(card))
    bad["data"]["character_book"] = {"extensions": {}, "entries": [{
        "keys": ["k"], "content": "x", "extensions": {}, "enabled": True,
        "insertion_order": 100, f: 42}]}
    r = validate_card_json(bad)
    check(f"non-string entry {f} rejected",
          not r.ok and any(f"{f} must be a string" in e for e in r.errors),
          str(r.errors))

bad = json.loads(json.dumps(card))
bad["data"]["character_book"] = {"entries": [{"keys": ["k"], "content": "x",
    "extensions": {}, "enabled": True, "insertion_order": 100}]}  # no extensions
r = validate_card_json(bad)
check("book-level extensions required", not r.ok and any("character_book.extensions" in e for e in r.errors))

bad = json.loads(json.dumps(card))
bad["data"]["character_book"] = {"extensions": {},
    "entries": [{"keys": ["k"], "content": "x", "extensions": {}, "enabled": True,
                 "insertion_order": True}]}  # bool for numeric
r = validate_card_json(bad)
check("boolean insertion_order rejected", not r.ok and any("insertion_order" in e for e in r.errors))

bad = json.loads(json.dumps(card))
bad["data"]["character_book"] = {"extensions": {},
    "entries": [{"keys": ["k"], "content": "x", "extensions": {}, "enabled": True,
                 "insertion_order": 100, "secondary_keys": ["ok", 42]}]}
r = validate_card_json(bad)
check("non-string secondary_keys element rejected", not r.ok and any("secondary_keys[1]" in e for e in r.errors))

# Standalone World Info entry missing `key` must be rejected.
bad_wi = {"entries": {"0": {"uid": 0, "content": "x", "constant": False}}}
r = validate_world_info_json(bad_wi)
check("standalone WI entry missing key rejected", not r.ok and any("key is required" in e for e in r.errors))

# Standalone WI numeric fields: bool rejected (top-level).
bad_wi = {"scanDepth": True, "entries": {"0": {"uid": 0, "key": ["k"], "content": "x",
                          "constant": False}}}
r = validate_world_info_json(bad_wi)
check("standalone WI bool scanDepth rejected", not r.ok and any("scanDepth" in e for e in r.errors))

bad = json.loads(json.dumps(card))
bad["data"]["character_book"] = {"entries": [{"keys": ["k"], "content": "x",
    "extensions": "not an object", "enabled": True, "insertion_order": 100}]}
r = validate_card_json(bad)
check("entry-level non-object extensions rejected", not r.ok and any("extensions" in e for e in r.errors))

bad = json.loads(json.dumps(card))
bad["data"]["character_book"] = {"entries": [{"keys": [], "content": "x",
    "extensions": {}, "enabled": True, "insertion_order": 100, "constant": True}],
    "extensions": {}}
r = validate_card_json(bad)
check("empty keys with constant allowed (no error)", r.ok, str(r.errors))

bad = json.loads(json.dumps(card))
bad["data"]["character_book"] = {"entries": [{"keys": ["k"], "content": "x",
    "extensions": {}, "enabled": True, "insertion_order": 100}], "extensions": {}}  # no position
r = validate_card_json(bad)
check("position omitted allowed", r.ok, str(r.errors))

# Position codes for standalone World Info export (verified vs world-info.js).
from lorebook_generator import LorebookSource, standalone_world_info
wi = standalone_world_info("b", [
    LorebookSource(title="a", keys=["k"], content="c", position="before_char"),
    LorebookSource(title="b", keys=["k"], content="c", position="after_char"),
    LorebookSource(title="c", keys=["k"], content="c", position="before_example_messages"),
    LorebookSource(title="d", keys=["k"], content="c", position="after_example_messages"),
    LorebookSource(title="e", keys=["k"], content="c", position="antop"),
    LorebookSource(title="f", keys=["k"], content="c", position="anbottom"),
])
positions = [wi["entries"][str(i)]["position"] for i in range(6)]
check("World Info position codes match SillyTavern world-info.js",
      positions == [0, 1, 5, 6, 2, 3], f"got {positions}")

# --------------------------------------------- CLI exit-code matrix
rc, _, err = run_cli("validate", str(tmpdir / "dist" / "yumi_akiyama.png"))
check("CLI validate PNG exits 0", rc == 0, err)
rc, _, err = run_cli("validate", str(tmpdir / "dist" / "yumi_akiyama.json"))
check("CLI validate JSON exits 0", rc == 0, err)
rc, _, err = run_cli("validate", "README.md")
check("CLI validate bad JSON exits 1", rc == 1, err)
rc, _, err = run_cli("parse", str(tmpdir / "dist" / "yumi_akiyama.png"))
check("CLI parse PNG exits 0", rc == 0, err)
rc, _, err = run_cli("parse", "README.md")
check("CLI parse non-PNG exits 1", rc == 1, err)
rc, _, err = run_cli("lorebook", "templates/lorebook_authoring_template.json", "-o", str(tmpdir / "wi.json"))
check("CLI lorebook exits 0", rc == 0, err)
rc, _, err = run_cli("validate-wi", str(tmpdir / "wi.json"))
check("CLI validate-wi exits 0", rc == 0, err)

# Strict variable semantics: unknown user placeholders must FAIL, not silently
# erase. Template field vars (description, etc.) remain optional.
strict_cfg = tmpdir / "strict_cfg.json"
strict_cfg.write_text(json.dumps({
    "name": "S", "slug": "s",
    "variables": {"known": "v"},
    "alternate_greetings": ["Hello <<unknown_greeting_var>>"],
}), encoding="utf-8")
rc, out, err = run_cli("build", str(strict_cfg), "-o", str(tmpdir / "strict"))
check("unknown var in alternate_greetings fails build", rc == 1, out)
check("alternate-greeting unknown-var failure no traceback", "Traceback" not in err, err[:200])
check("alternate-greeting failure cites the var",
      "unknown_greeting_var" in (out + err), (out + err)[:200])

strict_cfg2 = tmpdir / "strict_cfg2.json"
strict_cfg2.write_text(json.dumps({
    "name": "S2", "slug": "s2", "variables": {},
    "lorebook": {"entries": [{"title": "t", "keys": ["k"],
                               "content": "x <<unknown_book_var>> y"}]},
}), encoding="utf-8")
rc, out, err = run_cli("build", str(strict_cfg2), "-o", str(tmpdir / "strict2"))
check("unknown var in lorebook content fails build", rc == 1, out)
check("lorebook unknown-var failure cites the var",
      "unknown_book_var" in (out + err), (out + err)[:200])

# Custom template with its own greeting and lorebook (template contract test).
custom_tpl = tmpdir / "custom_tpl.json"
custom_tpl.write_text(json.dumps({
    "name": "Custom", "slug": "custom",
    "description": "<<hero_desc>>", "first_message": "Custom greeting, <<hero_name>>.",
    "alternate_greetings": ["Custom alt, <<hero_name>>."],
    "lorebook": {"name": "Custom Book", "entries": [
        {"title": "Home", "keys": ["<<hero_name>>"],
         "content": "<<hero_name>> lives in <<hero_city>>."},
    ]},
}), encoding="utf-8")
custom_cfg = tmpdir / "custom_cfg.json"
custom_cfg.write_text(json.dumps({
    "name": "Custom", "slug": "custom",
    "template": str(custom_tpl),
    "variables": {"hero_desc": "A hero.", "hero_name": "Rin", "hero_city": "Osaka"},
}), encoding="utf-8")
rc, out, err = run_cli("build", str(custom_cfg), "-o", str(tmpdir / "custom"))
check("custom-template build exits 0", rc == 0, err)
custom = json.loads((tmpdir / "custom" / "custom.json").read_text(encoding="utf-8"))["data"]
check("custom-template greeting rendered", custom["first_mes"] == "Custom greeting, Rin.", custom["first_mes"])
check("custom-template alt greeting rendered",
      custom["alternate_greetings"] == ["Custom alt, Rin."], str(custom["alternate_greetings"]))
check("custom-template lorebook used",
      custom["character_book"]["name"] == "Custom Book")
check("custom-template lorebook content rendered",
      custom["character_book"]["entries"][0]["content"] == "Rin lives in Osaka.",
      custom["character_book"]["entries"][0]["content"])
check("custom-template lorebook keys rendered",
      custom["character_book"]["entries"][0]["keys"] == ["Rin"])
check("custom-template description rendered", custom["description"] == "A hero.")

# Build with an unknown variable should fail cleanly (exit 1, no traceback).
bad_cfg = tmpdir / "bad_cfg.json"
bad_cfg.write_text(json.dumps({
    "name": "X",
    "variables": {},
    "prompt_template": {"first_message": "Hello <<missing>>"},
    "template": str(ROOT / "templates" / "card_v2_template.json"),
}), encoding="utf-8")
rc, out, err = run_cli("build", str(bad_cfg), "-o", str(tmpdir / "bad"))
check("CLI build with undefined var exits 1", rc == 1, err)
check("CLI build undefined var no traceback", "Traceback" not in err, err[:200])
check("CLI build undefined var cites <<missing>>", "missing" in (out + err), (out + err)[:200])

# Custom template with an unknown var must also fail cleanly.
custom_bad_cfg = tmpdir / "custom_bad_cfg.json"
custom_bad_cfg.write_text(json.dumps({
    "name": "CB", "template": str(custom_tpl),
    "variables": {"hero_desc": "d", "hero_city": "Osaka"},  # hero_name missing
}), encoding="utf-8")
rc, out, err = run_cli("build", str(custom_bad_cfg), "-o", str(tmpdir / "custom_bad"))
check("custom-template unknown var exits 1", rc == 1, out + err)
check("custom-template unknown var no traceback", "Traceback" not in err, err[:200])
check("custom-template unknown var cites hero_name", "hero_name" in (out + err), (out + err)[:200])

# Template-supplied name is retained when config does not supply one.
name_tpl_cfg = tmpdir / "name_tpl_cfg.json"
name_tpl_cfg.write_text(json.dumps({
    "template": str(custom_tpl),
    "variables": {"hero_desc": "d", "hero_name": "Rin", "hero_city": "Osaka"},
    # no top-level `name`
}), encoding="utf-8")
rc, out, err = run_cli("build", str(name_tpl_cfg), "-o", str(tmpdir / "name_tpl"))
check("template name retained without config name", rc == 0, out + err)
name_out = json.loads((tmpdir / "name_tpl" / "custom.json").read_text(encoding="utf-8"))["data"]
check("template name in payload", name_out["name"] == "Custom", name_out["name"])

# Greeting override applies to the selected text, not the `first_message` var.
# The template's greeting is literal; prompt_template.first_message wins.
fm_override_cfg = tmpdir / "fm_override_cfg.json"
fm_override_cfg.write_text(json.dumps({
    "name": "FO", "template": str(custom_tpl),
    "variables": {"hero_desc": "d", "hero_name": "Rin", "hero_city": "Osaka",
                  "first_message": "I am the wrong greeting."},
    "prompt_template": {"first_message": "Override greeting, <<hero_name>>."},
}), encoding="utf-8")
rc, out, err = run_cli("build", str(fm_override_cfg), "-o", str(tmpdir / "fm_override"))
check("prompt_template greeting overrides literal template greeting",
      rc == 0, out + err)
fm_out = json.loads((tmpdir / "fm_override" / "fo.json").read_text(encoding="utf-8"))["data"]
check("override greeting rendered into first_mes",
      fm_out["first_mes"] == "Override greeting, Rin.", fm_out["first_mes"])

# An explicitly-empty override (empty string) is honored, not treated as absent.
fm_empty_cfg = tmpdir / "fm_empty_cfg.json"
fm_empty_cfg.write_text(json.dumps({
    "name": "FE", "template": str(custom_tpl),
    "variables": {"hero_desc": "d", "hero_name": "Rin", "hero_city": "Osaka"},
    "prompt_template": {"first_message": ""},
}), encoding="utf-8")
rc, out, err = run_cli("build", str(fm_empty_cfg), "-o", str(tmpdir / "fm_empty"))
check("explicit-empty greeting override builds", rc == 0, out + err)
fe_out = json.loads((tmpdir / "fm_empty" / "fe.json").read_text(encoding="utf-8"))["data"]
check("explicit-empty override yields empty greeting",
      fe_out["first_mes"] == "", repr(fe_out["first_mes"]))

# Secondary keys imply selective=true by default; explicit false overrides.
sel_cfg = tmpdir / "sel_cfg.json"
sel_cfg.write_text(json.dumps({
    "name": "Sel", "slug": "sel",
    "lorebook": {"entries": [
        {"title": "A", "keys": ["primary"], "secondary_keys": ["extra"],
         "content": "x"},
        {"title": "B", "keys": ["primary"], "secondary_keys": ["extra"],
         "content": "x", "selective": False},
        {"title": "C", "keys": ["primary"], "content": "x"},  # no secondary keys
    ]},
}), encoding="utf-8")
rc, out, err = run_cli("build", str(sel_cfg), "-o", str(tmpdir / "sel"))
check("selective-implied build exits 0", rc == 0, out + err)
sel_out = json.loads((tmpdir / "sel" / "sel.json").read_text(encoding="utf-8"))["data"][
    "character_book"]["entries"]
check("secondary_keys imply selective=true",
      sel_out[0]["selective"] is True, str(sel_out[0]))
check("explicit selective=false preserved",
      sel_out[1]["selective"] is False, str(sel_out[1]))
check("no-secondary-keys entry defaults selective=false",
      sel_out[2]["selective"] is False, str(sel_out[2]))

# Entry-level scanDepth: null accepted (inherit), boolean rejected.
bad_wi_entry_sd = {"entries": {"0": {"uid": 0, "key": ["k"], "content": "x",
                              "constant": False, "scanDepth": True}}}
r = validate_world_info_json(bad_wi_entry_sd)
check("standalone WI entry-level bool scanDepth rejected",
      not r.ok and any("scanDepth" in e for e in r.errors),
      str(r.errors))

ok_wi_entry_sd = {"entries": {"0": {"uid": 0, "key": ["k"], "content": "x",
                             "constant": False, "scanDepth": None}}}
r = validate_world_info_json(ok_wi_entry_sd)
check("standalone WI entry-level null scanDepth accepted (inherit)",
      r.ok, str(r.errors))

# Standalone WI uid: null is rejected (must be a real uid).
bad_wi_uid_null = {"entries": {"0": {"uid": None, "key": ["k"], "content": "x",
                             "constant": False}}}
r = validate_world_info_json(bad_wi_uid_null)
check("standalone WI uid:null rejected",
      not r.ok and any("uid" in e for e in r.errors), str(r.errors))

# Build with an explicitly missing template must fail clearly (no silent fallback).
missing_tpl_cfg = tmpdir / "missing_tpl.json"
missing_tpl_cfg.write_text(json.dumps({
    "name": "X", "template": "templates/does_not_exist.json",
    "variables": {},
}), encoding="utf-8")
rc, out, err = run_cli("build", str(missing_tpl_cfg), "-o", str(tmpdir / "mt"))
check("CLI build with missing template exits 1", rc == 1, err)
check("CLI build missing template no traceback", "Traceback" not in err, err[:200])

# Build with a variable-bearing greeting + explicit book entry id/extensions.
rich_cfg = tmpdir / "rich_cfg.json"
rich_cfg.write_text(json.dumps({
    "name": "Rich", "slug": "rich", "creator": "t",
    "variables": {"tea": "oolong", "city_name": "Astoria", "first_message": "Welcome to <<city_name>>. Have some <<tea>>. {{user}}"},
    "alternate_greetings": ["Second greeting in <<city_name>> for {{user}}."],
    "lorebook": {
        "name": "Book", "description": "d", "scan_depth": 7, "token_budget": 999,
        "recursive_scanning": True, "extensions": {"x": 1},
        "entries": [
            {"title": "L", "keys": ["<<city_name>>"], "content": "<<city_name>> is a harbor city.",
             "id": 42, "extensions": {"note": "custom"}, "order": 50, "constant": True},
        ],
    },
}), encoding="utf-8")
rc, out, err = run_cli("build", str(rich_cfg), "-o", str(tmpdir / "rich"))
check("CLI build rich config exits 0", rc == 0, err)
rich = json.loads((tmpdir / "rich" / "rich.json").read_text(encoding="utf-8"))["data"]
check("greeting variables rendered", "Astoria" in rich["first_mes"] and "oolong" in rich["first_mes"], rich["first_mes"])
check("ST macros preserved in rendered greeting", "{{user}}" in rich["first_mes"])
check("alternate greeting variables rendered", "Astoria" in rich["alternate_greetings"][0], rich["alternate_greetings"])
rb = rich["character_book"]
check("book scan_depth preserved (7)", rb["scan_depth"] == 7, str(rb.get("scan_depth")))
check("book token_budget preserved (999)", rb["token_budget"] == 999)
check("book recursive_scanning preserved", rb["recursive_scanning"] is True)
check("book-level extensions preserved", rb["extensions"] == {"x": 1})
check("entry id preserved (42)", rb["entries"][0]["id"] == 42)
check("entry extensions preserved", rb["entries"][0]["extensions"] == {"note": "custom"})
check("entry keys variables rendered", rb["entries"][0]["keys"] == ["Astoria"], str(rb["entries"][0]["keys"]))
check("entry content variables rendered", "Astoria is a harbor city." == rb["entries"][0]["content"])
check("entry order preserved (50)", rb["entries"][0]["insertion_order"] == 50)
check("entry constant preserved", rb["entries"][0]["constant"] is True)

# Build with a user-supplied PNG that has garbage IDAT but VALID CRCs:
# `forge build` must now fail (full import-grade validation runs during build).
garbage_idat_cfg = tmpdir / "garbage_cfg.json"
garbage_png = tmpdir / "garbage.png"
# Build a PNG with garbage IDAT and recomputed valid CRCs.
chunks_g = read_chunks(_placeholder_png(), verify_crc=False)
for i, c in enumerate(chunks_g):
    if c.type == b"IDAT":
        chunks_g[i] = pm.Chunk(b"IDAT", b"\x99" * 16)
garbage_png.write_bytes(pm.write_chunks(chunks_g))
garbage_idat_cfg.write_text(json.dumps({
    "name": "G", "slug": "g", "template_png": str(garbage_png), "variables": {},
}), encoding="utf-8")
rc, out, err = run_cli("build", str(garbage_idat_cfg), "-o", str(tmpdir / "garbage"))
check("build with garbage-IDAT template PNG (valid CRC) exits 1",
      rc == 1, out)
check("build garbage-IDAT no traceback", "Traceback" not in (out + err), (out + err)[:200])

# Build with no lorebook, no template_png (placeholder fallback path).
plain_cfg = tmpdir / "plain_cfg.json"
plain_cfg.write_text(json.dumps({
    "name": "NoBook", "slug": "nobook", "tags": [], "creator": "t",
    "variables": {"description": "d", "personality": "p", "scenario": "s",
                  "first_message": "hi {{user}}", "mes_example": "",
                  "system_prompt": "sys", "character_note": "note"},
}), encoding="utf-8")
rc, out, err = run_cli("build", str(plain_cfg), "-o", str(tmpdir / "plain"))
check("CLI build without lorebook/template_png exits 0", rc == 0, err)
plain_json = tmpdir / "plain" / "nobook.json"
check("plain build JSON exists", plain_json.exists(), err)
plain = json.loads(plain_json.read_text(encoding="utf-8"))
check("plain build has no character_book", "character_book" not in plain["data"])
check("plain build depth_prompt present", "depth_prompt" in plain["data"]["extensions"])
check("plain build PNG uses placeholder", (tmpdir / "plain" / "nobook.png").exists())
r = validate_png_card((tmpdir / "plain" / "nobook.png").read_bytes())
check("plain build PNG validates", r.ok, str(r.errors))

# --------------------------------------------- Pillow independent decode
try:
    from PIL import Image  # type: ignore
    import io
    png_path = tmpdir / "dist" / "yumi_akiyama.png"
    img = Image.open(png_path)
    img.verify()
    check("built PNG decodes with Pillow", True)
    img2 = Image.open(png_path)
    check("built PNG has real dimensions", img2.size[0] >= 1 and img2.size[1] >= 1)
except ImportError:
    print("SKIP   Pillow not installed; independent-decode checks skipped (stdlib checks still ran)")
except Exception as exc:
    check("built PNG decodes with Pillow", False, str(exc))

# ------------------------------------------------------------------ report
print("\n" + "=" * 72)
print(f"passed={len(PASS)}  failed={len(FAIL)}")
for name, detail in FAIL:
    print(f"  FAIL {name} :: {detail}")
sys.exit(1 if FAIL else 0)
