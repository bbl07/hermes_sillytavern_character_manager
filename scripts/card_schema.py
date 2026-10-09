"""Character Card V2 schema, template, and builder.

Field mapping between the authoring template and the spec (V2):
- name, description, personality, scenario, mes_example -> same names in `data`.
- first_message (authoring) -> first_mes (V2 spec).
- character_note (authoring) -> extensions.depth_prompt (SillyTavern's
  Character's Note: prompt + role + depth, injected at a fixed message depth).
- alternate_greetings -> alternate_greetings.
- lorebook -> data.character_book (embedded, V2-shaped).

References
----------
- V2 spec (field-by-field):
  https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md
- SillyTavern Character's Note / depth prompt:
  https://docs.sillytavern.app/usage/core-concepts/characterdesign/
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

SPEC = "chara_card_v2"
SPEC_VERSION = "2.0"

# SillyTavern stores depth_prompt.role as a string: 'system' | 'user' | 'assistant'
# (verified in SillyTavern src/endpoints/characters.js: role_value = data.depth_prompt_role ?? 'system')
ROLE_VALUES = ("system", "user", "assistant")


@dataclass
class DepthPrompt:
    """SillyTavern 'Character's Note': prompt injected at a fixed depth."""
    prompt: str
    role: str = "system"  # 'system' | 'user' | 'assistant' (string per ST source)
    depth: int = 4

    def to_dict(self) -> dict:
        return {"prompt": self.prompt, "role": self.role, "depth": self.depth}


@dataclass
class LorebookEntry:
    """One World Info / character_book entry (V2 shape)."""
    keys: list[str]
    content: str
    enabled: bool = True
    insertion_order: int = 100
    extensions: dict[str, Any] = field(default_factory=dict)
    case_sensitive: bool = False
    name: Optional[str] = None
    priority: int = 10
    id: Optional[int] = None
    comment: Optional[str] = None
    selective: bool = False
    secondary_keys: list[str] = field(default_factory=list)
    constant: bool = False
    position: str = "after_char"  # 'before_char' | 'after_char'

    def to_dict(self, next_id: int = 0) -> dict:
        return {
            "keys": list(self.keys),
            "content": self.content,
            "extensions": dict(self.extensions),
            "enabled": self.enabled,
            "insertion_order": self.insertion_order,
            "case_sensitive": self.case_sensitive,
            "name": self.name or "",
            "priority": self.priority,
            "id": self.id if self.id is not None else next_id,
            "comment": self.comment or "",
            "selective": self.selective,
            "secondary_keys": list(self.secondary_keys),
            "constant": self.constant,
            "position": self.position,
        }


@dataclass
class CharacterBook:
    """V2 `character_book`: embedded lorebook, lives under `data`."""
    entries: list[LorebookEntry] = field(default_factory=list)
    name: str = ""
    description: str = ""
    scan_depth: int = 10
    token_budget: int = 2048
    recursive_scanning: bool = False
    extensions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        entries = []
        for i, e in enumerate(self.entries):
            entries.append(e.to_dict(next_id=i))
        return {
            "name": self.name,
            "description": self.description,
            "scan_depth": self.scan_depth,
            "token_budget": self.token_budget,
            "recursive_scanning": self.recursive_scanning,
            "extensions": dict(self.extensions),
            "entries": entries,
        }


@dataclass
class CharacterCardV2:
    """The V2 card. All authoring fields map 1:1 to the spec except
    `first_message` -> `first_mes` and `character_note` -> depth_prompt."""
    name: str
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_message: str = ""
    mes_example: str = ""
    creator_notes: str = ""
    system_prompt: str = ""
    post_history_instructions: str = ""
    alternate_greetings: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    creator: str = ""
    character_version: str = "1.0"
    extensions: dict[str, Any] = field(default_factory=dict)
    character_book: Optional[CharacterBook] = None
    character_note: Optional[DepthPrompt] = None

    def to_dict(self) -> dict:
        data: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "personality": self.personality,
            "scenario": self.scenario,
            "first_mes": self.first_message,
            "mes_example": self.mes_example,
            "creator_notes": self.creator_notes,
            "system_prompt": self.system_prompt,
            "post_history_instructions": self.post_history_instructions,
            "alternate_greetings": list(self.alternate_greetings),
            "tags": list(self.tags),
            "creator": self.creator,
            "character_version": self.character_version,
            "extensions": dict(self.extensions),
        }
        if self.character_book is not None:
            data["character_book"] = self.character_book.to_dict()
        if self.character_note is not None:
            # Character's Note is a SillyTavern extension on V2 cards.
            data["extensions"]["depth_prompt"] = self.character_note.to_dict()
        return {"spec": SPEC, "spec_version": SPEC_VERSION, "data": data}


def from_template(template: dict) -> CharacterCardV2:
    """Build a card from the authoring template at templates/card_v2_template.json.

    Accepts both the authoring names (`first_message`, `character_note`,
    `lorebook`) and the spec names, so the template file can be hand-edited
    either way.
    """
    t = dict(template)
    note_raw = t.pop("character_note", None) or t.pop("depth_prompt", None)
    book_raw = t.pop("lorebook", None) or t.pop("character_book", None)
    fm = t.pop("first_message", None) or t.pop("first_mes", None) or ""
    t.pop("first_mes", None)

    note = None
    if note_raw:
        if isinstance(note_raw, str):
            note = DepthPrompt(prompt=note_raw)
        else:
            note = DepthPrompt(
                prompt=note_raw.get("prompt", ""),
                role=note_raw.get("role", "system"),
                depth=int(note_raw.get("depth", 4)),
            )

    book = None
    if book_raw:
        book = CharacterBook(
            name=book_raw.get("name", ""),
            description=book_raw.get("description", ""),
            scan_depth=int(book_raw.get("scan_depth", 10)),
            token_budget=int(book_raw.get("token_budget", 2048)),
            recursive_scanning=bool(book_raw.get("recursive_scanning", False)),
            extensions=book_raw.get("extensions", {}),
            entries=[
                LorebookEntry(
                    keys=list(e.get("keys", [])),
                    content=e.get("content", ""),
                    # authoring `disable` inverts to spec `enabled`
                    enabled=not bool(e.get("disable", False)) if "disable" in e else bool(e.get("enabled", True)),
                    insertion_order=int(e.get("order", e.get("insertion_order", 100))),
                    case_sensitive=bool(e.get("case_sensitive", False)),
                    name=e.get("title") or e.get("name"),
                    priority=int(e.get("priority", 10)),
                    id=int(e["id"]) if isinstance(e.get("id"), int) else None,
                    comment=e.get("comment"),
                    selective=bool(e.get("selective", False)),
                    secondary_keys=list(e.get("secondary_keys", [])),
                    constant=bool(e.get("constant", False)),
                    position=e.get("position", "after_char"),
                    extensions=e.get("extensions", {}) or {},
                )
                for e in book_raw.get("entries", [])
            ],
        )

    return CharacterCardV2(
        name=t.get("name", ""),
        description=t.get("description", ""),
        personality=t.get("personality", ""),
        scenario=t.get("scenario", ""),
        first_message=fm,
        mes_example=t.get("mes_example", ""),
        creator_notes=t.get("creator_notes", ""),
        system_prompt=t.get("system_prompt", ""),
        post_history_instructions=t.get("post_history_instructions", ""),
        alternate_greetings=list(t.get("alternate_greetings", [])),
        tags=list(t.get("tags", [])),
        creator=t.get("creator", ""),
        character_version=t.get("character_version", "1.0"),
        extensions=t.get("extensions", {}) or {},
        character_book=book,
        character_note=note,
    )
