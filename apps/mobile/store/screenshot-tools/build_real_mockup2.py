import io
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from psd_tools import PSDImage
from psd_tools.constants import Tag
from scipy import ndimage


def find_coeffs(dst_pts, src_pts):
    matrix = []
    for (x, y), (X, Y) in zip(dst_pts, src_pts):
        matrix.append([x, y, 1, 0, 0, 0, -X * x, -X * y])
        matrix.append([0, 0, 0, x, y, 1, -Y * x, -Y * y])
    A = np.array(matrix, dtype=np.float64)
    B = np.array(src_pts, dtype=np.float64).reshape(8)
    return np.linalg.solve(A, B)


def chroma_key_black(img, threshold=6):
    arr = np.asarray(img.convert("RGBA")).astype(np.int16)
    is_fg = ~np.all(np.abs(arr[..., :3] - 9) <= threshold, axis=-1)
    is_fg = ndimage.binary_opening(is_fg, structure=np.ones((3, 3)), iterations=2)
    alpha = (is_fg * 255).astype(np.uint8)
    arr[..., 3] = alpha
    out = Image.fromarray(arr.astype(np.uint8), "RGBA")
    a = out.split()[3].filter(ImageFilter.GaussianBlur(1.2))
    out.putalpha(a)
    return out


def contain_resize(img, target_w, target_h, bg_color):
    """Scale the whole screenshot down to fit entirely inside target_w x target_h
    - never crops - and pads with bg_color on whichever axis has room left over."""
    img = img.convert("RGB")
    src_ratio = img.width / img.height
    dst_ratio = target_w / target_h
    if src_ratio > dst_ratio:
        new_w = target_w
        new_h = int(new_w / src_ratio)
    else:
        new_h = target_h
        new_w = int(new_h * src_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", (target_w, target_h), bg_color)
    canvas.paste(img, ((target_w - new_w) // 2, (target_h - new_h) // 2))
    return canvas


def extract_true_screen_mask(base_rgba, dst_quad, src_w, src_h):
    """The mockup's own blank-screen render already has the real (squircle,
    not circular-arc) corner curve baked in - pull that exact shape back into
    source space via the inverse perspective transform instead of guessing a
    corner radius, so a forward-warp of masked content lines up pixel-for-pixel."""
    arr = np.asarray(base_rgba.convert("RGBA"))
    is_white = (arr[..., 0] > 200) & (arr[..., 1] > 200) & (arr[..., 2] > 200) & (arr[..., 3] > 200)
    is_white = ndimage.binary_fill_holes(is_white)  # ignore the Dynamic Island cutout
    mask_final = Image.fromarray((is_white * 255).astype(np.uint8), "L")

    src_rect = [(0, 0), (src_w, 0), (src_w, src_h), (0, src_h)]
    inv_coeffs = find_coeffs(src_rect, dst_quad)
    mask_source = mask_final.transform((src_w, src_h), Image.PERSPECTIVE, inv_coeffs, resample=Image.BICUBIC)
    return mask_source.filter(ImageFilter.GaussianBlur(1.0))


class Angle:
    """For the mockups-design.com 'iPhone/Design/Highlights/Noise' template family
    (no clipping-mask pixel layer - the iPhone bezel layer itself has the hole)."""

    def __init__(self, psd_path):
        psd = PSDImage.open(psd_path)
        self.canvas_size = psd.size

        self.design_layer = None
        self.highlights_layer = None

        def find(layer, path=""):
            for l in layer:
                p = path + "/" + l.name
                if p == "/Mockup/Design/Design" and l.kind == "smartobject":
                    self.design_layer = l
                if p == "/Mockup/Highlights/Highlights":
                    self.highlights_layer = l
                if l.name in ("Background", "Shadows", "Delete this layer"):
                    l.visible = False
                if l.is_group():
                    find(l, p)

        find(psd)

        pl = self.design_layer._record.tagged_blocks.get_data(Tag.PLACED_LAYER2)
        t = pl.transform
        self.dst_quad = [(t[0], t[1]), (t[2], t[3]), (t[4], t[5]), (t[6], t[7])]

        so = self.design_layer.smart_object
        inner = PSDImage.open(io.BytesIO(so.data))
        self.inner_size = inner.size

        full_vp = (0, 0, self.canvas_size[0], self.canvas_size[1])
        self.highlights_overlay = (
            self.highlights_layer.composite(viewport=full_vp) if self.highlights_layer else None
        )

        self.design_layer.visible = False
        base = psd.composite(force=True)  # phone body; this doc has no canvas alpha channel
        self.base = chroma_key_black(base)  # so psd-tools fills empty space with opaque black

        w, h = self.inner_size
        self.screen_mask = extract_true_screen_mask(self.base, self.dst_quad, w, h)

    def render(self, screenshot_path, statusbar_crop_px=0, edge_margin_frac=0.035):
        w, h = self.inner_size
        shot = Image.open(screenshot_path).convert("RGB")
        if statusbar_crop_px:
            draw = ImageDraw.Draw(shot)
            draw.rectangle([0, 0, shot.width, statusbar_crop_px], fill=(255, 255, 255))

        # the app's own UI (e.g. the savings banner) runs edge-to-edge, so
        # filling the whole screen leaves it touching the bezel with no
        # breathing room - inset it slightly and pad with its own bg color
        inset = int(min(w, h) * edge_margin_frac)
        inner_w, inner_h = w - 2 * inset, h - 2 * inset
        bg_color = shot.getpixel((2, 2))
        fitted = contain_resize(shot, inner_w, inner_h, bg_color)
        content = Image.new("RGB", (w, h), bg_color)
        content.paste(fitted, (inset, inset))
        content = content.convert("RGBA")

        # the phone's screen has rounded corners, but our screenshot is a plain
        # rectangle - without this mask the corners of the warped screenshot
        # poke out past the bezel's rounded edge
        content.putalpha(self.screen_mask)

        src = [(0, 0), (w, 0), (w, h), (0, h)]
        coeffs = find_coeffs(self.dst_quad, src)
        warped = content.transform(self.canvas_size, Image.PERSPECTIVE, coeffs, resample=Image.BICUBIC)

        out = self.base.copy()
        out.alpha_composite(warped)
        if self.highlights_overlay is not None:
            base_arr = np.asarray(out).astype(np.float32) / 255
            hi_arr = np.asarray(self.highlights_overlay.convert("RGBA")).astype(np.float32) / 255
            hi_rgb = hi_arr[..., :3]
            hi_a = hi_arr[..., 3:4]
            screened = 1 - (1 - base_arr[..., :3]) * (1 - hi_rgb)
            out_rgb = base_arr[..., :3] * (1 - hi_a) + screened * hi_a
            result = np.concatenate([out_rgb, base_arr[..., 3:4]], axis=-1)
            out = Image.fromarray((result * 255).astype(np.uint8), "RGBA")
        return out


if __name__ == "__main__":
    psd_path = sys.argv[1]
    screenshot_path = sys.argv[2]
    out_path = sys.argv[3]
    crop = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    angle = Angle(psd_path)
    result = angle.render(screenshot_path, statusbar_crop_px=crop)
    bbox = result.split()[3].getbbox()
    if bbox:
        pad = 20
        l, t, r, b = bbox
        l = max(0, l - pad)
        t = max(0, t - pad)
        r = min(result.width, r + pad)
        b = min(result.height, b + pad)
        result = result.crop((l, t, r, b))
    result.save(out_path)
    print("saved", out_path, result.size)
