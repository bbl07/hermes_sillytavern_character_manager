"""Package a character card: JSON, PNG (with user-supplied image), assets bundle.

Image handling:
- `template_png` (user-supplied PNG) becomes the card's primary image unchanged;
  no pixel resizing is performed, only tEXt metadata is added.
- Additional user images (character, environment, etc.) are copied verbatim
  into an `assets/` bundle. SillyTavern's V2 import does NOT display these
  automatically; the bundle is for distribution, and CharX (V3) is the
  multi-asset native path. This is stated honestly in the README.

References
----------
- SillyTavern import formats (PNG/JSON/CharX):
  https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/characters.js
- Character Card V3 (CharX, multi-asset):
  https://github.com/malfoyslastname/character-card-spec-v3
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from png_metadata import embed_card_png, parse_png_card, PngError

ALLOWED_ASSET_TYPES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".json", ".md", ".txt",
}


@dataclass
class AssetBundle:
    assets_dir: Path
    manifest: dict

    @property
    def size_bytes(self) -> int:
        return sum(f.stat().st_size for f in self.assets_dir.rglob("*") if f.is_file())


def package_card(
    card_json: dict,
    output_dir: Path | str,
    template_png_path: Optional[Path | str] = None,
    asset_paths: Optional[list[Path | str]] = None,
    slug: Optional[str] = None,
) -> dict:
    """Write `<slug>.json`, `<slug>.png`, and `<slug>/assets/` under output_dir.

    Returns a manifest describing what was produced.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = card_json.get("data", {}).get("name", "character")
    slug = slug or _slugify(name)
    json_path = out / f"{slug}.json"
    png_path = out / f"{slug}.png"

    json_path.write_text(
        json.dumps(card_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    template_bytes: Optional[bytes] = None
    if template_png_path is not None:
        template_bytes = Path(template_png_path).read_bytes()
        if template_bytes[:8] != b"\x89PNG\r\n\x1a\n":
            raise PngError(f"template image {template_png_path} is not a PNG file")
    png_bytes = embed_card_png(card_json, template_png=template_bytes)
    png_path.write_bytes(png_bytes)

    bundle: Optional[AssetBundle] = None
    if asset_paths:
        assets_dir = out / f"{slug}_assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        for src in asset_paths:
            src_path = Path(src)
            if src_path.suffix.lower() not in ALLOWED_ASSET_TYPES:
                raise ValueError(
                    f"asset {src_path.name}: unsupported type {src_path.suffix} "
                    f"(allowed: {sorted(ALLOWED_ASSET_TYPES)})"
                )
            shutil.copy2(src_path, assets_dir / src_path.name)
        bundle = AssetBundle(
            assets_dir=assets_dir,
            manifest={"assets": [Path(p).name for p in asset_paths]},
        )

    return {
        "json": json_path,
        "png": png_path,
        "assets_dir": bundle.assets_dir if bundle else None,
        "assets": bundle.manifest.get("assets", []) if bundle else [],
        "roundtrip_ok": parse_png_card(png_bytes) == card_json,
    }


def _slugify(name: str) -> str:
    cleaned = "".join(
        c if c.isalnum() else "_" for c in name.strip().lower()
    ).strip("_")
    return cleaned or "character"
