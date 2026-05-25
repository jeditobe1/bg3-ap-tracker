"""Generate the quest-marker map-pin icons.

PopTracker swaps a map_location's `chest_unopened_img` and `chest_opened_img`
based on the section's check state -- unopened shows the bright golden "!",
opened shows a dimmed version. The icons are small (sized to map_location's
`location_size` in maps.json, currently 16px).

We draw an exclamation glyph centered on a transparent disc background
(rather than the default square chest) so quest pins are visually distinct
from kill pins (which use the default state-colored square).

Outputs go to images/items/ alongside the rest of the pack icons.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PACK = Path(__file__).resolve().parent.parent
OUT_DIR = PACK / "images" / "items"
SIZE = 32  # render larger; PopTracker scales down. Sharper edges that way.


def render_marker(unopened: bool, out_path: Path) -> None:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if unopened:
        bg_fill = (255, 200, 32, 230)     # golden disc
        bg_outline = (60, 40, 0, 255)
        glyph_fill = (40, 24, 0, 255)
    else:
        bg_fill = (88, 80, 60, 200)       # dim disc
        bg_outline = (40, 36, 28, 255)
        glyph_fill = (180, 168, 140, 255)

    # Disc background.
    pad = 1
    draw.ellipse((pad, pad, SIZE - pad - 1, SIZE - pad - 1),
                 fill=bg_fill, outline=bg_outline, width=2)

    # Exclamation glyph drawn as a thick stem + dot. Hand-drawn shapes scale
    # cleaner than rendered text at this size and avoid font-availability
    # issues across platforms.
    stem_w = SIZE // 5
    stem_x0 = (SIZE - stem_w) // 2
    stem_x1 = stem_x0 + stem_w
    stem_y0 = SIZE // 5
    stem_y1 = SIZE - SIZE // 5 - stem_w - 2
    draw.rounded_rectangle((stem_x0, stem_y0, stem_x1, stem_y1),
                           radius=stem_w // 2, fill=glyph_fill)

    dot_size = stem_w + 1
    dot_x0 = (SIZE - dot_size) // 2
    dot_y0 = SIZE - SIZE // 5 - dot_size
    draw.ellipse((dot_x0, dot_y0, dot_x0 + dot_size, dot_y0 + dot_size),
                 fill=glyph_fill)

    img.save(out_path)
    print(f"wrote {out_path.relative_to(PACK)}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    render_marker(unopened=True, out_path=OUT_DIR / "quest_marker_unopened.png")
    render_marker(unopened=False, out_path=OUT_DIR / "quest_marker_opened.png")


if __name__ == "__main__":
    main()
