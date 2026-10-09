#!/usr/bin/env python3
"""forge: build, package, and validate SillyTavern character cards.

Standalone-runnable (Python 3.10+, stdlib only) and callable by Hermes Agent
via SKILL.md. Subcommands:
  validate  <file>       Validate a .json card or .png card against ST import spec
  validate-wi <file>     Validate a standalone World Info (lorebook) JSON
  build     <config>     Build + package a card from a build config JSON
  parse     <file>       Extract the embedded card JSON from a PNG
  lorebook  <file>       Convert an authoring lorebook to ST World Info JSON

Run `forge.py <subcommand> --help` for details.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from card_schema import CharacterBook, CharacterCardV2, DepthPrompt, LorebookEntry
from lorebook_generator import LorebookSource, standalone_world_info, to_character_book, write_json
from packager import package_card
from png_metadata import parse_png_card, PngError
from prompt_parser import render_prompt, substitute_variables
from validate_cards import (
    validate_card_json, validate_png_card, validate_png_roundtrip, validate_world_info_json,
)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _load_json(path: Path | str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _render_prompt_fields(
    template_path: Path,
    variables: dict,
    tone_preset: Optional[str],
    extra_first_message: Optional[str] = None,
) -> dict:
    """Render the authoring template with variables + tone preset.

    SillyTavern macros ({{char}}/{{user}}) are preserved. The template's own
    field variables (description, personality, ...) default to the empty
    string when omitted from `variables`; arbitrary user placeholders must
    be supplied in `variables` or a `KeyError` is raised.

    `extra_first_message` (from config `prompt_template`) OVERRIDES the
    template's `first_message` when present.
    """
    tpl = _load_json(template_path)
    note = tpl.get("character_note", "")
    if isinstance(note, dict):
        note = note.get("prompt", "")
    # Greeting selection: `prompt_template.first_message` (when the key is
    # present, INCLUDING an explicit empty string) overrides the template's
    # greeting. The override text is itself rendered through `variables`
    # (strict; unknown `<<var>>` raises).
    if extra_first_message is not None:
        first_message_text = extra_first_message
        # Bounded fixed-point: a variable's value may contain other <<var>>.
        for _ in range(4):
            new_fm = substitute_variables(first_message_text, variables)
            if new_fm == first_message_text:
                break
            first_message_text = new_fm
    else:
        first_message_text = tpl.get("first_message", "")
    rendered = render_prompt(
        {
            "first_message": first_message_text,
            "description": tpl.get("description", ""),
            "personality": tpl.get("personality", ""),
            "scenario": tpl.get("scenario", ""),
            "mes_example": tpl.get("mes_example", ""),
            "system_prompt": tpl.get("system_prompt", ""),
            "character_note": note,
        },
        dict(variables),
        tone_preset,
        allow_template_fields=True,  # template field vars optional; user vars strict
    )
    return rendered


def _build_book(cfg_book: dict, variables: dict) -> CharacterBook:
    """Build the embedded character_book, preserving all supplied settings.

    Entry `content` and `keys` are run through `substitute_variables` (strict:
    any `<<var>>` must be present in `variables` or the build fails).
    """
    entries: list[LorebookEntry] = []
    for i, e in enumerate(cfg_book.get("entries", [])):
        entries.append(LorebookEntry(
            keys=[substitute_variables(k, variables) for k in e.get("keys", [])],
            content=substitute_variables(e.get("content", ""), variables),
            enabled=not bool(e.get("disable", False)),
            insertion_order=int(e.get("order", 100)),
            case_sensitive=bool(e.get("case_sensitive", False)),
            name=e.get("title") or e.get("name"),
            priority=int(e.get("priority", 10)),
            id=int(e["id"]) if isinstance(e.get("id"), int) else i,
            comment=e.get("comment"),
            # Secondary keys imply selective matching unless explicitly disabled.
            selective=bool(e.get("selective", bool(e.get("secondary_keys")))),
            secondary_keys=[
                substitute_variables(k, variables)
                for k in (e.get("secondary_keys") or [])
            ],
            constant=bool(e.get("constant", False)),
            position=e.get("position", "after_char"),
            extensions=e.get("extensions", {}) or {},
        ))
    return CharacterBook(
        name=cfg_book.get("name", ""),
        description=cfg_book.get("description", ""),
        scan_depth=int(cfg_book.get("scan_depth", 10)),
        token_budget=int(cfg_book.get("token_budget", 2048)),
        recursive_scanning=bool(cfg_book.get("recursive_scanning", False)),
        extensions=cfg_book.get("extensions", {}) or {},
        entries=entries,
    )


def _cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if path.suffix.lower() == ".png":
        try:
            blob = path.read_bytes()
        except OSError as exc:
            print(f"error: could not read {path}: {exc}", file=sys.stderr)
            return 1
        report = validate_png_card(blob)
    else:
        try:
            blob = path.read_bytes()
            card = json.loads(blob.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            print(f"error: could not read/parse {path}: {exc}", file=sys.stderr)
            return 1
        report = validate_card_json(card)
    print(report)
    return 0 if report.ok else 1


def _cmd_validate_wi(args: argparse.Namespace) -> int:
    path = Path(args.file)
    try:
        wi = _load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read/parse {path}: {exc}", file=sys.stderr)
        return 1
    report = validate_world_info_json(wi)
    print(report)
    return 0 if report.ok else 1


def _cmd_build(args: argparse.Namespace) -> int:
    try:
        cfg = _load_json(args.config)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read build config {args.config}: {exc}", file=sys.stderr)
        return 1

    # Resolve the authoring template: config override, else bundled default.
    template_cfg = cfg.get("template")
    template_path: Optional[Path] = None
    if template_cfg:
        template_path = Path(template_cfg)
        if not template_path.is_absolute():
            candidate = Path(args.config).parent / template_path
            template_path = candidate if candidate.exists() else template_path
        if not template_path.exists():
            print(f"error: template not found: {template_path}", file=sys.stderr)
            return 1  # explicit template supplied but missing -> fail, don't fall back
    if template_path is None:
        template_path = TEMPLATES_DIR / "card_v2_template.json"
    if not template_path.exists():
        print(f"error: template not found: {template_path}", file=sys.stderr)
        return 1

    try:
        rendered = _render_prompt_fields(
            template_path,
            cfg.get("variables", {}),
            cfg.get("tone_preset"),
            extra_first_message=(cfg.get("prompt_template") or {}).get("first_message") if cfg.get("prompt_template") else None,
        )
    except (KeyError, TypeError) as exc:
        print(f"error: prompt rendering failed: {exc}", file=sys.stderr)
        return 1

    # Lorebook: config `lorebook` overrides the template's; if only the template
    # supplies one, use it (variables rendered strict inside _build_book).
    book_cfg = cfg.get("lorebook")
    if book_cfg is None:
        tpl_book = _load_json(template_path).get("lorebook")
        # Only use a template lorebook if it has actual entries; a shape-only
        # template block (empty entries) is not a lorebook.
        if tpl_book and tpl_book.get("entries"):
            book_cfg = tpl_book
    try:
        book = _build_book(book_cfg, cfg.get("variables", {})) if book_cfg else None
    except KeyError as exc:
        print(f"error: lorebook rendering failed: {exc}", file=sys.stderr)
        return 1

    # Alternate greetings: template-supplied are the base; config overrides
    # (config entries are rendered strict: any `<<var>>` must be in variables).
    tpl_alt_greetings = list(_load_json(template_path).get("alternate_greetings", []))
    cfg_alt = cfg.get("alternate_greetings")
    alt_greetings = list(cfg_alt) if cfg_alt is not None else tpl_alt_greetings
    try:
        alt_greetings = [
            substitute_variables(g, cfg.get("variables", {}))
            for g in alt_greetings
        ]
    except KeyError as exc:
        print(f"error: alternate_greetings rendering failed: {exc}", file=sys.stderr)
        return 1

    dp = None
    if rendered.character_note:
        raw_note = _load_json(template_path).get("character_note")
        role = "system"
        depth = 4
        if isinstance(raw_note, dict):
            role = raw_note.get("role", "system")
            depth = int(raw_note.get("depth", 4))
        dp = DepthPrompt(prompt=rendered.character_note, role=role, depth=depth)

    card = CharacterCardV2(
        # Config name takes precedence; template-supplied name is the fallback.
        name=cfg.get("name") or _load_json(template_path).get("name") or "character",
        description=rendered.description,
        personality=rendered.personality,
        scenario=rendered.scenario,
        first_message=rendered.first_message,
        mes_example=rendered.mes_example,
        creator_notes=cfg.get("creator_notes", ""),
        system_prompt=rendered.system_prompt,
        post_history_instructions=cfg.get("post_history_instructions", ""),
        alternate_greetings=alt_greetings,
        tags=list(cfg.get("tags", [])),
        creator=cfg.get("creator", ""),
        character_version=cfg.get("character_version", "1.0"),
        character_book=book,
        character_note=dp,
    )
    card_dict = card.to_dict()

    report = validate_card_json(card_dict)
    if not report.ok:
        print(report, file=sys.stderr)
        return 1

    try:
        manifest = package_card(
            card_dict,
            args.output or "dist",
            template_png_path=cfg.get("template_png"),
            asset_paths=cfg.get("assets"),
            slug=cfg.get("slug"),
        )
    except (OSError, PngError, ValueError) as exc:
        print(f"error: packaging failed: {exc}", file=sys.stderr)
        return 1

    rt = validate_png_roundtrip(card_dict, manifest["png"].read_bytes())
    if not rt.ok:
        print(rt, file=sys.stderr)
        return 1

    # Full import-grade validation: ST-faithful reader semantics + Pillow pixel
    # decode. Catches a user-supplied template PNG whose pixels don't decode.
    iv = validate_png_card(manifest["png"].read_bytes())
    if not iv.ok:
        print(iv, file=sys.stderr)
        return 1

    print(json.dumps({
        "ok": True,
        "json": str(manifest["json"]),
        "png": str(manifest["png"]),
        "assets_dir": str(manifest["assets_dir"] or ""),
        "assets": manifest["assets"],
        "roundtrip_ok": True,
        "import_validated": True,
        "applied_preset": rendered.applied_preset,
        "macros_found": rendered.macros_found,
    }, indent=2))
    return 0


def _cmd_parse(args: argparse.Namespace) -> int:
    try:
        card = parse_png_card(Path(args.file).read_bytes())
    except (PngError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(card, ensure_ascii=False, indent=2))
    return 0


def _cmd_lorebook(args: argparse.Namespace) -> int:
    try:
        src = _load_json(args.file)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read {args.file}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(src, (list, dict)):
        print(f"error: lorebook source must be a list or object", file=sys.stderr)
        return 1
    if isinstance(src, list):
        sources = [LorebookSource(**e) for e in src]
        wi = standalone_world_info("lorebook", sources)
    else:
        sources = [LorebookSource(**e) for e in src.get("entries", [])]
        wi = standalone_world_info(
            name=src.get("name", "lorebook"),
            sources=sources,
            description=src.get("description", ""),
            scan_depth=int(src.get("scan_depth", 4)),
            token_budget=int(src.get("token_budget", 512)),
            recursive_scanning=bool(src.get("recursive_scanning", True)),
        )
    r = validate_world_info_json(wi)
    if not r.ok:
        print(r, file=sys.stderr)
        return 1
    out = write_json(args.output, wi)
    print(json.dumps({"ok": True, "output": str(out)}, indent=2))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="forge")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate")
    v.add_argument("file")

    vw = sub.add_parser("validate-wi")
    vw.add_argument("file")

    b = sub.add_parser("build")
    b.add_argument("config")
    b.add_argument("--output", "-o")

    pa = sub.add_parser("parse")
    pa.add_argument("file")

    l = sub.add_parser("lorebook")
    l.add_argument("file")
    l.add_argument("--output", "-o", required=True)

    args = p.parse_args(argv)
    return {
        "validate": _cmd_validate,
        "validate-wi": _cmd_validate_wi,
        "build": _cmd_build,
        "parse": _cmd_parse,
        "lorebook": _cmd_lorebook,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
