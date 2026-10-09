# Standalone SillyTavern World Info JSON — Spec Summary

A standalone lorebook (imported via SillyTavern's World Info panel) has a different shape from the V2 embedded `character_book`. Authoritative community spec: [lore-genius sillytavern_lorebook_specification.md](https://github.com/PedroPareja/lore-genius/blob/main/sillytavern_lorebook_specification.md).

## Top level

```json
{
  "entries": { "0": { "...": "..." }, "1": { "...": "..." } },
  "name": "Lorebook Name",
  "description": "Optional",
  "scanDepth": 4,
  "tokenBudget": 512,
  "recursiveScanning": true,
  "extensions": {}
}
```

`entries` is an **object keyed by stringified uid** (not an array).

## Entry shape

```json
{
  "uid": 0,
  "key": ["primary", "triggers"],
  "keysecondary": ["optional", "filters"],
  "comment": "Entry title / memo",
  "content": "Lore text injected into the prompt.",
  "constant": false,
  "selective": false,
  "selectiveLogic": 0,
  "order": 100,
  "position": 1,
  "disable": false,
  "excludeRecursion": false,
  "probability": 100,
  "useProbability": true,
  "depth": 4,
  "group": "",
  "groupOverride": false,
  "groupWeight": 100,
  "scanDepth": null,
  "caseSensitive": false,
  "matchWholeWords": true,
  "useGroupScoring": null,
  "automationId": "",
  "role": 0,
  "vectorized": false,
  "sticky": 0,
  "cooldown": 0,
  "delay": 0
}
```

## Key semantics

- `selectiveLogic`: 0=AND_ANY, 1=NOT_ALL, 2=NOT_ANY, 3=AND_ALL.
- `position` codes (verified in [SillyTavern world-info.js](https://github.com/SillyTavern/SillyTavern/blob/release/public/scripts/world-info.js)):
  - `0` = before_char (before character defs)
  - `1` = after_char (after character defs)
  - `2` = ANTop (top of Author's Note)
  - `3` = ANBottom (bottom of Author's Note)
  - `4` = atDepth (at a specific chat depth)
  - `5` = EMTop / before_example_messages
  - `6` = EMBottom / after_example_messages
  - `7` = outlet (with `{{outlet::Name}}`)

**Note:** positions 2 and 3 are Author's Note positions, not example-message positions. Example-message positions are 5 and 6.
- `constant: true` = always inserted (no keyword needed).
- `probability`: 100 = inserted on every activation; 0 = never.
- `weight` (not always exported) is the budget priority; higher = kept under budget.
- `matchWholeWords` defaults to true at the entry level.

## Sources

- [lore-genius — SillyTavern lorebook specification](https://github.com/PedroPareja/lore-genius/blob/main/sillytavern_lorebook_specification.md)
- [SillyTavern docs — World Info](https://docs.sillytavern.app/usage/core-concepts/worldinfo/)
- [SillyTavern world-info.js (source)](https://github.com/SillyTavern/SillyTavern/blob/release/public/scripts/world-info.js)
