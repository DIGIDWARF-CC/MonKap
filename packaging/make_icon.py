"""Draws MonKap's icon (a frame's four corners around a dot) and writes packaging/monkap.ico and monkap.png.
Run: python packaging/make_icon.py"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024                                   # drawn big, then scaled down for each icon size


def draw() -> Image.Image:
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((40, 40, S - 40, S - 40), radius=210, fill=(30, 37, 48, 255), outline=(58, 69, 85, 255), width=10)
    blue, bar, margin, length = (59, 130, 246, 255), 74, 210, 250
    for right in (False, True):
        for bottom in (False, True):
            x = S - margin if right else margin
            y = S - margin if bottom else margin
            dx, dy = (-1 if right else 1), (-1 if bottom else 1)
            d.rounded_rectangle((min(x, x + dx * length), y - bar // 2, max(x, x + dx * length), y + bar // 2),
                                radius=bar // 2, fill=blue)
            d.rounded_rectangle((x - bar // 2, min(y, y + dy * length), x + bar // 2, max(y, y + dy * length)),
                                radius=bar // 2, fill=blue)
    d.ellipse((S / 2 - 95, S / 2 - 95, S / 2 + 95, S / 2 + 95), fill=(240, 244, 248, 255))
    return im


if __name__ == "__main__":
    big = draw()
    big.save(os.path.join(HERE, "monkap.ico"), sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)])
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(HERE, "monkap.png"))
    print("wrote", os.path.join(HERE, "monkap.ico"))
