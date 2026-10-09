# Hermes ST Card Forge

A modular project for generating, formatting, validating, and packaging **SillyTavern character cards** (Character Card V2 JSON / PNG), including lorebooks, tone-tuned prompts, and user-supplied images.

Packaged as a Hermes Agent skill (`SKILL.md`) but **standalone-runnable**: Python 3.10+. Dependencies:

- **Core pipeline** (lorebook, JSON validation, PNG metadata): stdlib only.
- **Import validation** (`build` and `validate` on a `.png` file, `st_import.py`): requires [Pillow](https://pypi.org/project/Pillow/) for a real pixel decode (`Image.load()`). Install with `pip install Pillow`. A successful validated build therefore requires Pillow.

## Directory layout

```
hermes-st-card-forge/
├── SKILL.md                       # Hermes Agent skill entrypoint
├── README.md                      # This file
├── assets/examples/
│   ├── portrait_example.png       # Sample user-supplied primary image
│   └── environment_example.png    # Sample user-supplied environment image
├── scripts/
│   ├── forge.py                   # CLI: build / validate / validate-wi / parse / lorebook
│   ├── card_schema.py             # V2 data model + authoring-name mapping
│   ├── lorebook_generator.py      # Standalone World Info + embedded character_book
│   ├── prompt_parser.py           # Tone presets + `<<var>>` substitution
│   ├── png_metadata.py            # tEXt/zTXt embed/extract (stdlib PNG codec)
│   ├── st_import.py               # Conservative ST-faithful import validation (needs Pillow)
│   ├── packager.py                # JSON + PNG + asset bundle writer
│   └── validate_cards.py          # Import-contract validator
├── templates/
│   ├── card_v2_template.json      # Authoring template (first_message, character_note)
│   ├── example_build_config.json  # Annotated build config
│   └── lorebook_authoring_template.json
├── references/
│   ├── card_v2_spec.md            # V2 field summary + Character's Note
│   └── world_info_spec.md         # Standalone lorebook JSON shape
├── tests/
│   ├── test_forge.py              # 35 positive + negative checks
│   └── test_integration.py        # 123 integration + hardening checks
└── dist/                          # Build output (JSON, PNG, assets/)
```

## Quick start

```bash
# Run the test suite
python3 tests/test_forge.py

# Build a card from the example config
python3 scripts/forge.py build templates/example_build_config.json -o dist

# Validate the outputs
python3 scripts/forge.py validate dist/yumi_akiyama.png
python3 scripts/forge.py validate dist/yumi_akiyama.json

# Extract a card back out of a PNG
python3 scripts/forge.py parse dist/yumi_akiyama.png

# Convert an authoring lorebook to SillyTavern World Info JSON
python3 scripts/forge.py lorebook templates/lorebook_authoring_template.json -o dist/book.json
```

## What the modules do

### `card_schema.py` — V2 data model

Builds `{"spec": "chara_card_v2", "spec_version": "2.0", "data": {...}}` per the [V2 spec](https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md). The authoring template uses friendly names, which are mapped on output:

| Authoring name | SillyTavern V2 field | Notes |
|---|---|---|
| `first_message` | `data.first_mes` | First message / greeting |
| `character_note` | `data.extensions.depth_prompt` | [Character's Note](https://docs.sillytavern.app/usage/core-concepts/characterdesign/): `{prompt, role, depth}`; `role` is a **string** (`'system'`/`'user'`/`'assistant'`), verified in [SillyTavern characters.js](https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/characters.js) |
| `lorebook` | `data.character_book` | Embedded lorebook (V2 shape) |
| `alternate_greetings` | `data.alternate_greetings` | Swipes for the first message |

### `lorebook_generator.py` — two lorebook shapes

- **Standalone World Info JSON** (imported via SillyTavern's World Info panel): `entries` keyed by stringified `uid`, with `key` / `keysecondary` / `constant` / `order` / `position` codes. Shape per the [lore-genius spec](https://github.com/PedroPareja/lore-genius/blob/main/sillytavern_lorebook_specification.md).
- **Embedded `character_book`** (inside the card): `entries` is an array with `keys` / `id` ints / `insertion_order` / `position` as `"before_char"` / `"after_char"`, per the [V2 spec](https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md).

These are deliberately distinct shapes; do not cross them.

### `prompt_parser.py` — tone tuning

- `<<variable>>` authoring placeholders (substituted).
- SillyTavern runtime macros `{{char}}` / `{{user}}` are preserved verbatim (only `<<...>>` is matched).
- Built-in tone presets: `literary`, `casual`, `noir`, `epic`. Custom presets can be registered via `register_preset(TonePreset(...))`.
- Undefined variables raise `KeyError` (no silent empty substitution).

### `png_metadata.py` — PNG card embedding

Pure-stdlib PNG chunk codec. Writes follow the exact convention SillyTavern reads ([character-card-parser.js](https://github.com/SillyTavern/SillyTavern/blob/release/src/character-card-parser.js)):

- Card JSON is UTF-8, base64-encoded, stored in a `tEXt` chunk with keyword `chara`, inserted immediately before `IEND`.
- Reads accept both tEXt and zTXt (zlib), and the V3 keyword `ccv3` (which takes precedence on read).
- Embedding a card into a user-supplied PNG leaves pixel data (`IDAT`) unchanged; only metadata is added. Re-embedding replaces any existing `chara`/`ccv3` chunks **case-insensitively** (SillyTavern matches keywords case-insensitively) so exactly one payload remains.
- `read_chunks` verifies CRC per chunk and requires IHDR + IDAT; the fallback 1x1 RGBA placeholder PNG is a real, decodable image (verified with Pillow in the test suite).

### `st_import.py` — SillyTavern import validation (conservative)

A separate, stricter validator modeled on SillyTavern's reader (verified against [character-card-parser.js](https://github.com/SillyTavern/SillyTavern/blob/release/src/character-card-parser.js)):

- Reads `tEXt` chunks ONLY (no zTXt, no iTXt).
- Matches keywords case-insensitively.
- Checks `ccv3` FIRST; falls back to `chara`.
- Returns the FIRST matching chunk (first-wins).
- Decodes the text as base64 (Python strict `validate=True`; SillyTavern's Node `Buffer.from(..., 'base64')` is more permissive, so this is a **conservative** compatibility check, not an exact importer clone).
- Forces a real pixel decode with Pillow (`Image.load()`), because a PNG with valid metadata but a corrupt/unusable image is not importable in practice.

`validate_png_card` in `validate_cards.py` uses this strict importer, and `forge build` runs it after packaging. `parse_png_card` (used for round-trip checks) remains the permissive tooling extractor.

### `packager.py` — user-supplied images

- `template_png`: user-supplied PNG becomes the card's **primary image** unchanged (no resize).
- `assets`: additional images (character alternates, environment, etc.) are copied verbatim into `dist/<slug>_assets/`.
- **Honest caveat:** SillyTavern's V2 PNG import does not automatically display additional images after import — the bundle is for distribution. CharX (V3) is the native multi-asset path; see [V3 spec](https://github.com/malfoyslastname/character-card-spec-v3) and [SillyTavern's CharX parser](https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/characters.js).

### `validate_cards.py` — import-contract checks

- V2 JSON: required fields (`name` through `extensions`), types, `spec`/`spec_version` envelope (string `'2.0'` only), depth_prompt shape (string role), character_book shape (book-level `extensions` required; entry `position` optional; empty keys + constant allowed; `secondary_keys` element types; booleans rejected for numeric fields), and rejection of authoring-only names leaking into `data`.
- Standalone World Info JSON: `entries` object, uid uniqueness, required `key`/`content` types, `extensions`/nested-field type checks.
- PNG: conservative ST-importer semantics (tEXt-only, case-insensitive, ccv3-before-chara, first-wins, strict base64), real Pillow pixel decode, CRC per chunk, IHDR/IDAT presence, round-trip identity (PNG payload == source JSON), 10 MB size cap. Validators never raise; malformed input yields an invalid Report / nonzero CLI exit. `forge build` runs this full validation before reporting success.

### Standalone World Info position codes

Verified against [SillyTavern world-info.js](https://github.com/SillyTavern/SillyTavern/blob/release/public/scripts/world-info.js): `0=before_char, 1=after_char, 2=ANTop, 3=ANBottom, 4=atDepth, 5=EMTop(before example messages), 6=EMBottom(after example messages), 7=outlet`. Positions 2/3 are Author's Note positions — not example-message positions (those are 5/6).

## Test coverage

`python3 tests/test_forge.py` runs 35 checks and `python3 tests/test_integration.py` runs 123 checks (all passing). Coverage includes:

- **Full build integration**: builds the actual `templates/example_build_config.json` and verifies every rendered field (greeting, description, personality, note, book settings, alternate greetings).
- **Custom-template contract**: a custom template with its own greeting, alternate_greetings, and lorebook is honored (config supplies variables; template supplies the base; config `prompt_template` overrides).
- **Strict variable semantics**: unknown user placeholders in alternate_greetings, lorebook content, and prompt_template text FAIL the build (no silent erase). The standard template field names (description, personality, ...) remain optional with an empty-string default.
- **Variable rendering**: `<<var>>` substitution in greetings (including a variable's value containing other `<<var>>`s), alternate greetings, and lorebook entry keys/content; ST macros `{{user}}` preserved.
- **Lorebook preservation**: explicit entry `id`, `extensions`, `order`, `constant`, book `scan_depth`/`token_budget`/`recursive_scanning`/`extensions`.
- **PNG hardening**: placeholder fallback decodes with Pillow; corrupted IDAT with **valid CRC** rejected via pixel decode; mismatched CRC; missing IDAT; zTXt-only rejected by the conservative ST importer; raw-JSON payload rejected; duplicate `chara` chunks first-wins; uppercase `CCV3` stripped case-insensitively.
- **Build-level validation**: a build using a user-supplied PNG with garbage IDAT but valid CRCs exits 1 (full import-grade validation runs during `forge build`).
- **Schema hardening**: non-object root, bad `extensions`, numeric `spec_version`, `creator`/`character_version` types, non-string `alternate_greetings` elements, string-only `depth_prompt.role`, list-valued enum rejected without traceback, book-level `extensions` required, boolean rejected in numeric fields, `secondary_keys` element types, standalone-WI missing `key` rejected.
- **CLI exit codes**: every subcommand (build, validate, validate-wi, parse, lorebook) returns 0 or 1 cleanly; undefined-variable, missing-template, garbage-PNG-template, and non-PNG `template_png` failures exit 1 without a traceback.
- **World Info position codes**: exported numeric values asserted against SillyTavern's `world-info.js` enumeration.

**Scope note:** this validates against the documented SillyTavern import contract (spec-level), not against a live SillyTavern build. Always do a final manual import in SillyTavern before publishing a card.

## Sources

- [Character Card V2 spec](https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md)
- [SillyTavern docs — Character Design](https://docs.sillytavern.app/usage/core-concepts/characterdesign/)
- [SillyTavern docs — World Info](https://docs.sillytavern.app/usage/core-concepts/worldinfo/)
- [SillyTavern docs — FAQ (PNG import errors)](https://docs.sillytavern.app/usage/faq/)
- [SillyTavern character-card-parser.js](https://github.com/SillyTavern/SillyTavern/blob/release/src/character-card-parser.js)
- [SillyTavern endpoints/characters.js (import formats incl. CharX)](https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/characters.js)
- [lore-genius — SillyTavern lorebook specification](https://github.com/PedroPareja/lore-genius/blob/main/sillytavern_lorebook_specification.md)
- [Hermes Agent — Creating Skills](https://hermes-agent.nousresearch.com/docs/developer-guide/creating-skills)
- [Hermes Agent — Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)

## License

MIT.
