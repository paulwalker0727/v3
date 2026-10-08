"""
Genereaza un fisier icon.ico placeholder (cerc gri cu textul „C").
Ruleaza o singura data pentru a avea un icon minim in build:
    python make_icon.py

Inlocuieste ulterior 'icon.ico' cu iconita ta reala.
"""
from PIL import Image, ImageDraw, ImageFont
import os

SIZES = [16, 32, 48, 64, 128, 256]

def make_placeholder(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # cerc de fundal
    d.ellipse((2, 2, size - 2, size - 2), fill=(70, 90, 130, 255), outline=(200, 210, 230, 255), width=max(1, size // 32))
    # litera „C" in centru
    try:
        font = ImageFont.truetype("arial.ttf", int(size * 0.55))
    except Exception:
        font = ImageFont.load_default()
    text = "C"
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    d.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
           text, fill=(255, 255, 255, 255), font=font)
    return img

def main():
    imgs = [make_placeholder(s) for s in SIZES]
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
    imgs[0].save(out, format="ICO", sizes=[(s, s) for s in SIZES], append_images=imgs[1:])
    print(f"OK - iconita creata: {out}")

if __name__ == "__main__":
    main()
