---
name: sillytavern-card-forge
description: Generate, format, validate, and package SillyTavern character cards (V2 JSON/PNG) with lorebooks, tone-tuned prompts, and user-supplied images.
version: 1.0.0
author: hermes-st-card-forge
license: MIT
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [sillytavern, character-cards, roleplay, lorebook, json, png]
    category: creative
    related_skills: []
---

# SillyTavern Character Card Forge

Standalone Python toolkit (Python 3.10+; requires [Pillow](https://pypi.org/project/Pillow/) for the import-grade pixel-decode validation used by `build` and `validate`) for building SillyTavern character cards that pass the V2 import contract. All commands run directly; Hermes installation is not required.

## When to Use

- User asks to create, format, validate, or package a SillyTavern / TavernAI character card.
- User provides character/environment PNG images to embed in a card.
- User wants a lorebook (World Info) JSON or a prompt template with tone tuning.

## Quick Reference

| Command | Purpose |
|---|---|
| `python3 scripts/forge.py build <config.json> -o dist` | Build + package a card (JSON + PNG + assets) |
| `python3 scripts/forge.py validate <file>` | Validate a .json or .png card against ST import spec |
| `python3 scripts/forge.py validate-wi <file>` | Validate a standalone World Info (lorebook) JSON |
| `python3 scripts/forge.py parse <card.png>` | Extract the embedded card JSON from a PNG |
| `python3 scripts/forge.py lorebook <src.json> -o <out.json>` | Convert authoring lorebook to ST World Info JSON |
| `python3 tests/test_forge.py` | Run the core test suite (35 checks) |
| `python3 tests/test_integration.py` | Run the integration suite (123 checks) |

## Procedure

1. **Gather inputs** from the user: character details, tone preference (literary/casual/noir/epic), lorebook entries (optional), and PNG images (optional: primary portrait, environment).
2. **Write a build config** following `templates/example_build_config.json`. Key fields:
   - `name`, `tags`, `creator`, `slug`
   - `tone_preset` (optional; see scripts/prompt_parser.py `PRESETS`)
   - `template` (optional; defaults to `templates/card_v2_template.json`)
   - `template_png`: user-supplied PNG that becomes the card's primary image (pixels unchanged)
   - `assets`: additional images copied verbatim into `<slug>_assets/`
   - `variables`: values for `<<name>>` placeholders in the template
   - `lorebook.entries`: list of `{title, keys, secondary_keys, content, constant, order, priority, position, ...}`
3. **Build**: `python3 scripts/forge.py build <config.json> -o dist`. This renders prompts, applies the tone preset, builds the V2 card, embeds it in the PNG as a `chara` tEXt chunk (base64 JSON), and runs FULL import-grade validation (conservative ST reader semantics + Pillow pixel decode) before reporting success. Undefined `<<var>>`s, non-PNG `template_png`, and a template PNG with garbage pixels all exit 1 with a clear message, never a traceback.
4. **Report** the `dist/<slug>.json` and `dist/<slug>.png` files. The JSON imports via Character Management; the PNG via drag-and-drop.

## Field mapping (authoring name -> spec name)

| Authoring | SillyTavern V2 | Notes |
|---|---|---|
| `first_message` | `data.first_mes` | |
| `character_note` | `data.extensions.depth_prompt` | Character's Note: `{prompt, role, depth}`; `role` is a **string** (`'system'`/`'user'`/`'assistant'`), verified in SillyTavern's src/endpoints/characters.js |
| `lorebook` | `data.character_book` | Embedded, V2 shape |
| `alternate_greetings` | `data.alternate_greetings` | |

SillyTavern runtime macros `{{char}}` / `{{user}}` pass through verbatim. Only `<<name>>` authoring variables are substituted; unknown ones raise a `KeyError`.

## Pitfalls

- **Plain PNGs fail import.** SillyTavern requires a `chara` (or `ccv3`) tEXt chunk. Some file managers strip metadata when re-saving — always distribute the raw `forge.py` output.
- **Authoring names leaking into spec payload.** The validator rejects `first_message` / `character_note` / `lorebook` inside `data`; they must be mapped.
- **Both lorebook shapes are distinct.** Standalone World Info JSON uses `entries` keyed by uid string; embedded `character_book` uses an `entries` array with `id` ints. Do not reuse one shape for the other.
- **Multiple images in V2.** A V2 PNG import does not display extra character/environment images. They are bundled for distribution; CharX (V3) is the multi-asset native path.
- **zTXt vs tEXt.** Writes use tEXt; reads accept both. A WEBP renamed `.png` will fail the signature check (as SillyTavern's FAQ notes). SillyTavern's own reader handles `tEXt` chunks only, and both `build` and `validate` require Pillow for the import-grade pixel-decode check.
- **depth_prompt.role is a string.** SillyTavern stores `role` as `'system'`/`'user'`/`'assistant'` (characters.js), not a numeric code. A numeric role is rejected by the validator.
- **World Info position codes.** `2=ANTop, 3=ANBottom` are Author's Note positions, NOT example-message positions. Example-message positions are `5=EMTop, 6=EMBottom` (verified in SillyTavern's world-info.js). Codes: 0=before_char, 1=after_char, 4=atDepth, 7=outlet.

## Hermes installation

Copy this directory into your Hermes profile's skills tree:

```bash
mkdir -p ~/.hermes/skills/creative
cp -r hermes-st-card-forge ~/.hermes/skills/creative/
```

Or install from a URL after hosting the directory:

```
/skills install https://example.com/hermes-st-card-forge/SKILL.md --name sillytavern-card-forge
```

Working-directory convention: run all commands from the **skill's root** (the directory containing `SKILL.md`), so relative paths like `templates/example_build_config.json` and `assets/examples/` resolve. The scripts are also fully runnable standalone outside Hermes.

## Verification

Run the full suite; both must report `failed=0`:

```bash
cd <skill-dir> && python3 tests/test_forge.py && python3 tests/test_integration.py
```

For a given card file:

```bash
python3 scripts/forge.py validate dist/<slug>.png   # expect: VALID
```

This is a spec-level check against the documented SillyTavern import contract; it is not a substitute for a live SillyTavern import test.

## References

Load on demand:
- `skill_view("sillytavern-card-forge", "references/card_v2_spec.md")` — V2 field summary + Character's Note semantics
- `skill_view("sillytavern-card-forge", "references/world_info_spec.md")` — standalone lorebook JSON shape
- `templates/example_build_config.json` — annotated build config
- `templates/card_v2_template.json` — authoring template
