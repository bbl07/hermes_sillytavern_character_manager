"""Lorebook generator: standalone SillyTavern World Info JSON + embedded V2 character_book.

Two distinct shapes are produced deliberately:
- Standalone `.json` for SillyTavern's World Info panel import.
- `data.character_book` (V2) embedded in the card itself.

References
----------
- Standalone World Info JSON shape:
  https://github.com/PedroPareja/lore-genius/blob/main/sillytavern_lorebook_specification.md
- V2 character_book:
  https://github.com/malfoyslastname/character-card-spec-v2
- SillyTavern World Info UI semantics:
  https://docs.sillytavern.app/usage/core-concepts/worldinfo/
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from card_schema import CharacterBook, LorebookEntry

# SillyTavern position codes for the standalone World Info format.
# Verified in SillyTavern public/scripts/world-info.js:
#   world_info_position = { before:0, after:1, ANTop:2, ANBottom:3,
#                           atDepth:4, EMTop:5, EMBottom:6, outlet:7 }
POSITION_CODES = {
    "before_char": 0,
    "after_char": 1,
    "antop": 2,
    "anbottom": 3,
    "at_depth": 4,
    "before_example_messages": 5,  # EMTop
    "after_example_messages": 6,   # EMBottom
    "outlet": 7,
}
ROLE_CODES = {"system": 0, "user": 1, "assistant": 2}


@dataclass
class LorebookSource:
    """An authoring-side lore entry that can be exported to either format."""
    title: str  # memo / comment; not used by the AI
    keys: list[str]
    content: str
    secondary_keys: list[str] = None  # type: ignore[assignment]
    constant: bool = False
    order: int = 100  # insertion order
    priority: int = 10  # weight; lower = discarded first under budget
    position: str = "after_char"
    role: str = "system"  # only used for @D-style entries
    group: str = ""
    disable: bool = False
    case_sensitive: bool = False
    match_whole_words: bool = True
    scan_depth: Optional[int] = None
    probability: int = 100

    def __post_init__(self) -> None:
        if self.secondary_keys is None:
            self.secondary_keys = []


def _standalone_entry(uid: int, e: LorebookSource) -> dict:
    """One entry in SillyTavern's standalone World Info shape."""
    return {
        "uid": uid,
        "key": list(e.keys),
        "keysecondary": list(e.secondary_keys or []),
        "comment": e.title,
        "content": e.content,
        "constant": e.constant,
        "selective": bool(e.secondary_keys),
        "selectiveLogic": 0,  # AND_ANY
        "order": e.order,
        "position": POSITION_CODES.get(e.position, 1),
        "disable": e.disable,
        "excludeRecursion": False,
        "probability": e.probability,
        "useProbability": True,
        "depth": 4,
        "group": e.group,
        "groupOverride": False,
        "groupWeight": 100,
        "scanDepth": e.scan_depth,
        "caseSensitive": e.case_sensitive,
        "matchWholeWords": e.match_whole_words,
        "useGroupScoring": None,
        "automationId": "",
        "role": ROLE_CODES[e.role],
        "vectorized": False,
        "sticky": 0,
        "cooldown": 0,
        "delay": 0,
    }


def standalone_world_info(
    name: str,
    sources: list[LorebookSource],
    description: str = "",
    scan_depth: int = 4,
    token_budget: int = 512,
    recursive_scanning: bool = True,
) -> dict:
    """Build the standalone SillyTavern World Info JSON (entries keyed by uid)."""
    entries = {str(i): _standalone_entry(i, e) for i, e in enumerate(sources)}
    return {
        "entries": entries,
        "name": name,
        "description": description,
        "scanDepth": scan_depth,
        "tokenBudget": token_budget,
        "recursiveScanning": recursive_scanning,
        "extensions": {},
    }


def to_character_book(
    name: str,
    sources: list[LorebookSource],
    description: str = "",
    scan_depth: int = 10,
    token_budget: int = 2048,
    recursive_scanning: bool = False,
) -> CharacterBook:
    """Build the V2 `character_book` object for embedding in a card."""
    return CharacterBook(
        name=name,
        description=description,
        scan_depth=scan_depth,
        token_budget=token_budget,
        recursive_scanning=recursive_scanning,
        entries=[
            LorebookEntry(
                keys=e.keys,
                content=e.content,
                enabled=not e.disable,
                insertion_order=e.order,
                case_sensitive=e.case_sensitive,
                name=e.title,
                priority=e.priority,
                comment=e.title,
                selective=bool(e.secondary_keys),
                secondary_keys=list(e.secondary_keys or []),
                constant=e.constant,
                position=e.position,
            )
            for e in sources
        ],
    )


def write_json(path: Path | str, obj: dict) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
