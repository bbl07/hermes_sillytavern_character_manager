# SillyTavern Character Card V2 — Spec Summary

Authoritative source: [malfoyslastname/character-card-spec-v2](https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md).

## Envelope

```json
{
  "spec": "chara_card_v2",
  "spec_version": "2.0",
  "data": { "...": "..." }
}
```

`spec` and `spec_version` are required at the top level. All character fields live under `data`.

## Required `data` fields

V1 carryovers (all strings): `name`, `description`, `personality`, `scenario`, `first_mes`, `mes_example`.
V2 additions: `creator_notes`, `system_prompt`, `post_history_instructions` (strings), `alternate_greetings` (array of strings), `tags` (array of strings), `creator` (string), `character_version` (string), `extensions` (object, mandatory — may be empty).

Optional: `character_book` (object).

## PNG embedding

The card JSON is base64-encoded UTF-8 and stored in a PNG `tEXt` chunk with keyword `chara`, inserted immediately before `IEND`. SillyTavern also reads keyword `ccv3` (V3) with precedence, and accepts zTXt (zlib) on read. Reference: [SillyTavern character-card-parser.js](https://github.com/SillyTavern/SillyTavern/blob/release/src/character-card-parser.js).

## Character's Note (depth_prompt)

SillyTavern's Character's Note is a prompt injected at a fixed message depth to reinforce character traits ([docs.sillytavern.app — Character Design](https://docs.sillytavern.app/usage/core-concepts/characterdesign/)). On V2 cards it is stored as an extension:

```json
"extensions": {
  "depth_prompt": {
    "prompt": "Note text...",
    "role": "system",
    "depth": 4
  }
}
```

`role` is a **string**: `'system'` | `'user'` | `'assistant'`. Verified in [SillyTavern's src/endpoints/characters.js](https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/characters.js):

```js
const role_default = 'system';
_.set(char, 'data.extensions.depth_prompt.role', data.depth_prompt_role ?? 'system');
```

`depth` is the number of messages back from the newest message; 0 means after the last message.

## character_book (V2 shape)

```json
"character_book": {
  "name": "", "description": "",
  "scan_depth": 10, "token_budget": 2048, "recursive_scanning": false,
  "extensions": {},
  "entries": [
    {
      "keys": ["trigger"],
      "content": "Lore text.",
      "extensions": {},
      "enabled": true,
      "insertion_order": 100,
      "case_sensitive": false,
      "name": "",
      "priority": 10,
      "id": 0,
      "comment": "",
      "selective": false,
      "secondary_keys": [],
      "constant": false,
      "position": "after_char"
    }
  ]
}
```

`position`: `"before_char"` or `"after_char"` (relative to character definitions). Lower `insertion_order` = inserted higher in context. Lower `priority` = discarded first under budget.

## Sources

- [Character Card V2 spec](https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md)
- [SillyTavern docs — Character Design](https://docs.sillytavern.app/usage/core-concepts/characterdesign/)
- [SillyTavern docs — FAQ (PNG import errors)](https://docs.sillytavern.app/usage/faq/)
- [SillyTavern character-card-parser.js (source)](https://github.com/SillyTavern/SillyTavern/blob/release/src/character-card-parser.js)
- [SillyTavern endpoints/characters.js (source — depth_prompt role is a string)](https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/characters.js)
