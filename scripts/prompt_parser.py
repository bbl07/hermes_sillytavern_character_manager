"""Prompt template parser for roleplay tone tuning.

Design rules (per Hermes advisor review):
- SillyTavern runtime macros like `{{char}}` and `{{user}}` are preserved
  verbatim; only documented authoring variables are substituted.
- Tone presets are appended as System-block guidance; they never rewrite
  user-authored card text.
- Missing variables are a hard error, not a silent empty substitution.

References
----------
- SillyTavern macros: https://docs.sillytavern.app/usage/core-concepts/macros/
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

# Authoring variable: `<<name>>`. Distinct from ST's `{{macro}}`.
_AUTHORING_VAR = re.compile(r"<<([a-zA-Z_][a-zA-Z0-9_]*)>>")

# SillyTavern runtime macros that must pass through untouched.
_ST_MACROS = [
    "char", "user", "persona", "original", "charlink",
    "lastmessage", "lastusermessage", "lastassistentmessage",
    "time", "weekday", "date", "datestamp", "isodate",
    "idle_duration", "random", "pick", "roll", "outlet", "newline",
]


@dataclass
class TonePreset:
    name: str
    system_guidance: str  # appended to system_prompt
    depth_guidance: str = ""  # appended to character_note (depth_prompt.prompt)


# Registered tone presets. Users may add more via `register_preset`.
PRESETS: dict[str, TonePreset] = {
    "literary": TonePreset(
        name="literary",
        system_guidance=(
            "Write in a literary third-person style: rich sensory detail, "
            "measured pacing, interiority for {{char}} only. Avoid "
            "second-person narration. Keep replies under 300 words unless "
            "the scene demands more."
        ),
        depth_guidance="Maintain literary prose; no OOC chatter.",
    ),
    "casual": TonePreset(
        name="casual",
        system_guidance=(
            "Keep the tone light and conversational. Short paragraphs, "
            "contractions, occasional humor. Match {{user}}'s energy; do not "
            "monologue."
        ),
    ),
    "noir": TonePreset(
        name="noir",
        system_guidance=(
            "Hardboiled noir voice: terse, cynical, concrete imagery, "
            "weather and light as mood. Third person, past tense preferred."
        ),
    ),
    "epic": TonePreset(
        name="epic",
        system_guidance=(
            "High-fantasy epic register: elevated diction, formal address, "
            "foreshadowing. Avoid anachronisms and modern slang."
        ),
    ),
}


def register_preset(preset: TonePreset) -> None:
    PRESETS[preset.name] = preset


# Variable names used by the bundled authoring template. When rendering the
# bundled template these are optional and default to the empty string; a card
# with no personality is valid. Arbitrary user-introduced placeholders
# (e.g. `<<tea>>`) remain strict.
TEMPLATE_FIELD_VARS = {
    "description", "personality", "scenario", "first_message", "mes_example",
    "system_prompt", "character_note",
}


def substitute_variables(
    text: str,
    variables: dict[str, str],
    defaults: Optional[dict[str, str]] = None,
    allow_template_fields: bool = False,
) -> str:
    """Replace `<<name>>` authoring variables.

    `defaults` maps variable name -> fallback value when missing from
    `variables`.

    `allow_template_fields=True` additionally treats the standard template
    field names (description, personality, scenario, first_message,
    mes_example, system_prompt, character_note) as optional with an empty-
    string default. Arbitrary user placeholders still raise `KeyError`.

    SillyTavern `{{macro}}` syntax is left untouched because the regex only
    matches `<<...>>`.
    """
    if not isinstance(text, str):
        raise TypeError(f"template text must be str, got {type(text).__name__}")
    missing: list[str] = []

    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in variables:
            return str(variables[name])
        if isinstance(defaults, dict) and name in defaults:
            return str(defaults[name])
        if allow_template_fields and name in TEMPLATE_FIELD_VARS:
            return ""
        missing.append(name)
        return m.group(0)

    out = _AUTHORING_VAR.sub(repl, text)
    if missing:
        unique = sorted(set(missing))
        raise KeyError(
            f"undefined authoring variable(s) in template: {', '.join(unique)}"
        )
    return out


def validate_macros_preserved(text: str) -> list[str]:
    """Return a list of `{{macro}}` names present (for caller-side audit)."""
    found = re.findall(r"\{\{\s*([a-zA-Z_]+)\s*\}\}", text)
    return sorted(set(found))


@dataclass
class RenderedPrompt:
    first_message: str
    description: str = ""
    personality: str = ""
    scenario: str = ""
    mes_example: str = ""
    system_prompt: str = ""
    character_note: str = ""
    applied_preset: Optional[str] = None
    macros_found: list[str] = field(default_factory=list)


def render_prompt(
    template: dict,
    variables: dict[str, str],
    tone_preset: Optional[str] = None,
    defaults: Optional[dict[str, str]] = None,
    allow_template_fields: bool = False,
) -> RenderedPrompt:
    """Substitute variables into a prompt template and apply a tone preset.

    `template` keys: name, description, personality, scenario, first_message,
    mes_example, system_prompt, character_note, alternate_greetings.
    `allow_template_fields=True` makes the standard template field variables
    optional (empty-string default).
    """
    preset = PRESETS.get(tone_preset) if tone_preset else None
    if tone_preset and preset is None:
        raise KeyError(
            f"unknown tone preset '{tone_preset}'. "
            f"available: {sorted(PRESETS)}"
        )

    def sub(v: Optional[str]) -> str:
        return substitute_variables(
            v or "", variables, defaults=defaults, allow_template_fields=allow_template_fields
        ) if v else ""

    rendered = RenderedPrompt(
        first_message=sub(template.get("first_message")),
        description=sub(template.get("description")),
        personality=sub(template.get("personality")),
        scenario=sub(template.get("scenario")),
        mes_example=sub(template.get("mes_example")),
        system_prompt=sub(template.get("system_prompt")),
        character_note=sub(template.get("character_note")),
        applied_preset=preset.name if preset else None,
    )
    # A variable's value may itself contain `<<other_var>>`; resolve to a
    # fixed point (bounded) so `first_message` ends fully substituted.
    for _ in range(4):
        new_fm = substitute_variables(
            rendered.first_message, variables,
            defaults=defaults, allow_template_fields=allow_template_fields,
        )
        if new_fm == rendered.first_message:
            break
        rendered.first_message = new_fm
    if preset:
        rendered.system_prompt = (
            rendered.system_prompt.rstrip() + "\n\n" + preset.system_guidance
        ).strip()
        if preset.depth_guidance:
            rendered.character_note = (
                rendered.character_note.rstrip()
                + "\n"
                + preset.depth_guidance
            ).strip()

    all_text = " ".join(
        [
            rendered.first_message, rendered.description, rendered.personality,
            rendered.scenario, rendered.mes_example, rendered.system_prompt,
            rendered.character_note,
        ]
    )
    rendered.macros_found = validate_macros_preserved(all_text)
    return rendered
