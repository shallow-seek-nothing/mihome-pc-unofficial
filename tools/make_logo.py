"""Render the project's original PC controller logo (development tool).

The SVG beside the web assets is the editable source.  This Pillow renderer
uses the same geometry to produce the runtime PNG and Windows ICO files; the
application itself remains standard-library only.
"""
from pathlib import Path
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mihome_pc.tray import ico_from_png  # noqa: E402

STATIC = ROOT / "mihome_pc" / "static"
SCALE = 4


def render(size):
    canvas = size * SCALE

    def box(values):
        return tuple(round(value * canvas / 256) for value in values)

    def point(values):
        return tuple(round(value * canvas / 256) for value in values)

    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    pixels = image.load()
    start = (18, 63, 82)
    end = (20, 118, 111)
    for y in range(canvas):
        for x in range(canvas):
            amount = min(1.0, max(0.0, (x + y) / (2 * canvas)))
            pixels[x, y] = tuple(round(a + (b - a) * amount)
                                 for a, b in zip(start, end)) + (255,)
    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        box((8, 8, 248, 248)), radius=round(60 * canvas / 256), fill=255)
    image.putalpha(mask)
    draw = ImageDraw.Draw(image)
    white = "#F4FBFA"
    mint = "#82E6D4"
    width = round(14 * canvas / 256)
    draw.rounded_rectangle(box((48, 52, 208, 170)),
                           radius=round(16 * canvas / 256),
                           outline=white, width=width)
    draw.line((point((128, 170)), point((128, 198))), fill=white, width=width)
    draw.line((point((91, 205)), point((165, 205))), fill=white, width=width)
    node_width = round(8 * canvas / 256)
    for a, b in (((88, 105), (162, 85)), ((88, 105), (130, 142)),
                 ((162, 85), (130, 142))):
        draw.line((point(a), point(b)), fill=mint, width=node_width)
    for index, (x, y) in enumerate(((88, 105), (162, 85), (130, 142))):
        outline = start if index < 2 else end
        draw.ellipse(box((x - 15.5, y - 15.5, x + 15.5, y + 15.5)),
                     fill="#%02X%02X%02X" % outline)
        draw.ellipse(box((x - 10.5, y - 10.5, x + 10.5, y + 10.5)), fill=mint)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main():
    logo = render(256)
    logo_path = STATIC / "logo.png"
    logo.save(logo_path, optimize=True)
    render(64).save(STATIC / "favicon.png", optimize=True)
    ico_path = ROOT / "mihome_pc" / "assets" / "mihome.ico"
    ico_path.write_bytes(ico_from_png(logo_path.read_bytes()))
    print("wrote original PC controller logo: logo.png, favicon.png, mihome.ico")


if __name__ == "__main__":
    main()
