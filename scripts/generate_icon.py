# Purpose: Generate Windows 11 application icon (.ico and .png).
# What the code does:
#   - Draws a modern fluent-style icon with document badge and magnifying glass.
#   - Exports multi-resolution .ico containing 16x16, 32x32, 48x48, 64x64, 128x128, 256x256.
#   - Saves high-res 512x512 PNG.
# Usage notes, dependencies, or assumptions:
#   - Requires Pillow.

import os
from PIL import Image, ImageDraw


def generate_app_icon(output_dir: str):
    os.makedirs(output_dir, exist_ok=True)
    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Fluent background rounded tile
    draw.rounded_rectangle([32, 32, 480, 480], radius=110, fill=(37, 99, 235))
    
    # Inner subtle highlight
    draw.rounded_rectangle([44, 44, 468, 468], radius=98, outline=(96, 165, 250, 180), width=6)

    # 2. Document sheet representation
    doc_left, doc_top, doc_right, doc_bottom = 120, 110, 360, 410
    draw.rounded_rectangle([doc_left, doc_top, doc_right, doc_bottom], radius=24, fill=(255, 255, 255))
    
    # Document text lines
    line_color = (203, 213, 225)
    draw.rounded_rectangle([160, 180, 320, 196], radius=8, fill=line_color)
    draw.rounded_rectangle([160, 225, 300, 241], radius=8, fill=line_color)
    draw.rounded_rectangle([160, 270, 280, 286], radius=8, fill=line_color)

    # 3. Magnifying search glass (Cyan/Blue gradient accent)
    cx, cy, r = 295, 285, 80
    # Glass outer ring
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(14, 165, 233), width=24)
    # Glass interior fill
    draw.ellipse([cx - r + 12, cy - r + 12, cx + r - 12, cy + r - 12], fill=(224, 242, 254, 120))
    # Handle
    draw.line([cx + 55, cy + 55, cx + 125, cy + 125], fill=(14, 165, 233), width=28)

    # Save PNG
    png_path = os.path.join(output_dir, "app_icon.png")
    img.save(png_path, format="PNG")
    print(f"Generated: {png_path}")

    # Save multi-res ICO
    ico_path = os.path.join(output_dir, "app_icon.ico")
    icon_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(ico_path, format="ICO", sizes=icon_sizes)
    print(f"Generated: {ico_path}")


if __name__ == "__main__":
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_dir = os.path.join(project_root, "src", "doc_searcher", "assets")
    generate_app_icon(target_dir)
