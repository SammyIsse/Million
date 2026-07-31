import os
import sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter

FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "PlusJakartaSans-Variable.ttf")
W, H = 1290, 2796
BG = (214, 245, 227)
HEADLINE_COLOR = (4, 120, 87)
SUB_COLOR = (30, 90, 70)

def font(size, variation):
    f = ImageFont.truetype(FONT_PATH, size)
    f.set_variation_by_name(variation)
    return f

def wrap_text(draw, text, fnt, max_width):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=fnt) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

def render(headline, subhead, phone_png, out_path, phone_scale=0.62, gap_after_text=170, phone_x_offset=40):
    canvas = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(canvas)

    h_font = font(74, "Bold")
    s_font = font(38, "Medium")

    margin = 90
    max_w = W - 2 * margin
    h_lines = wrap_text(draw, headline, h_font, max_w)
    s_lines = wrap_text(draw, subhead, s_font, max_w) if subhead else []

    y = 120
    for line in h_lines:
        lw = draw.textlength(line, font=h_font)
        draw.text(((W - lw) / 2, y), line, font=h_font, fill=HEADLINE_COLOR)
        y += 88

    if s_lines:
        y += 16
        for line in s_lines:
            lw = draw.textlength(line, font=s_font)
            draw.text(((W - lw) / 2, y), line, font=s_font, fill=SUB_COLOR)
            y += 50

    phone = Image.open(phone_png).convert("RGBA")
    new_w = int(phone.width * phone_scale)
    new_h = int(phone.height * phone_scale)
    phone = phone.resize((new_w, new_h), Image.LANCZOS)

    px = (W - new_w) // 2 + phone_x_offset
    py = y + gap_after_text

    alpha = phone.split()[3]
    tint = Image.new("RGBA", phone.size, (8, 36, 26, 150))
    tint.putalpha(alpha.point(lambda a: int(a * 0.5)))

    shadow_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_offset_x = px - int(new_w * 0.06)
    shadow_offset_y = py + int(new_h * 0.02)
    shadow_layer.paste(tint, (shadow_offset_x, shadow_offset_y), tint)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(34))
    canvas.paste(shadow_layer, (0, 0), shadow_layer)

    canvas.paste(phone, (px, py), phone)

    canvas.save(out_path, "PNG")
    print("saved", out_path, canvas.size)

if __name__ == "__main__":
    render(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
