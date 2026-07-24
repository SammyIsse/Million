#!/usr/bin/env python3
"""Generér favicon/app-ikoner ud fra static/favicon.svg.

Køres MANUELT og lokalt (kræver macOS' qlmanage til at rasterisere SVG samt
Pillow). Resultatet committes, så hverken CI eller deploy afhænger af dette
script - se scripts/build-pages.sh, der kun kopierer de færdige filer.

    python3 scripts/build-icons.py

Tre varianter, fordi platformene maskerer forskelligt:

  rounded    Egne runde hjørner. Bruges til favicon.ico og manifestets
             purpose="any"-ikoner, hvor ingen maskerer for os.
  fullbleed  Firkantet, farven ud til kanten. Til apple-touch-icon: iOS
             runder SELV hjørnerne, så vores egne runde hjørner ville
             efterlade sorte trekanter i de fire ender.
  maskable   Firkantet med ekstra luft om glyffen. Android beskærer
             purpose="maskable" til en cirkel med kun de inderste ~80 %
             som sikker zone; glyffen holdes derfor mindre.
"""
from __future__ import annotations

import io
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
GREEN = "#059669"

# (translate_x, translate_y, scale) for kurv-glyffen i et 64x64-viewBox.
# Glyffens egne grænser er x 1..23, y 1..22 (22x21 enheder), så værdierne
# centrerer den og styrer hvor stor en del af fladen den fylder.
_GEOM = {
    "rounded": (13.4, 14.15, 1.55),    # ~53 % af fladen
    "fullbleed": (11.0, 11.85, 1.75),  # ~60 % - lidt større, kanten er farvet
    "maskable": (14.6, 15.35, 1.45),   # ~50 % - skal tåle cirkelbeskæring
}


def svg_source(variant: str, px: int) -> str:
    """SVG med eksplicit pixelstørrelse. qlmanage rasteriserer efter SVG'ens
    intrinsiske width/height, ikke efter -s, så størrelsen SKAL sættes her."""
    tx, ty, scale = _GEOM[variant]
    radius = 12 if variant == "rounded" else 0
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="{px}" height="{px}">
  <rect width="64" height="64" rx="{radius}" fill="{GREEN}"/>
  <g transform="translate({tx} {ty}) scale({scale})" fill="none" stroke="#FFFFFF"
     stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
    <path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>
  </g>
  <g transform="translate({tx} {ty}) scale({scale})" fill="#FFFFFF">
    <circle cx="9" cy="21" r="1.9"/>
    <circle cx="20" cy="21" r="1.9"/>
  </g>
</svg>
"""


def rounded_alpha(px: int, radius_units: float = 12.0) -> Image.Image:
    """Antialiaseret alpha-maske for det runde hjørne.

    Nødvendig, fordi qlmanage komponerer mod UGENNEMSIGTIGT HVIDT: uden masken
    bliver de fire hjørner hvide trekanter i stedet for transparente, og det
    ses tydeligt på en mørk browserfane eller hjemmeskærm. Masken tegnes i 4x
    og nedskaleres, da PIL ikke antialiaserer rounded_rectangle selv.
    """
    ss = 4
    mask = Image.new("L", (px * ss, px * ss), 0)
    radius = radius_units / 64.0 * px * ss
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, px * ss - 1, px * ss - 1), radius=radius, fill=255,
    )
    return mask.resize((px, px), Image.Resampling.LANCZOS)


# Alt rasteriseres i denne størrelse og nedskaleres derefter. Grunden er ikke
# bekvemmelighed: qlmanage returnerer et TOMT HVIDT thumbnail for små SVG'er
# (målt - 16, 32 og 48 kom tilbage uden en eneste grøn pixel, mens 180 og
# opefter var korrekte). Da SVG er opløsningsuafhængig, er geometrien den
# samme, og en LANCZOS-nedskalering fra 512 er reelt supersampling - pænere
# kanter end en native lille rastering ville have givet.
_MASTER_PX = 512
_GREEN_RGB = (5, 150, 105)


def _rasterize(variant: str) -> Image.Image:
    """Rasterisér én variant i _MASTER_PX via Quick Look."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        src = tmpdir / "icon.svg"
        src.write_text(svg_source(variant, _MASTER_PX), encoding="utf-8")
        subprocess.run(
            ["qlmanage", "-t", "-s", str(_MASTER_PX), "-o", str(tmpdir), str(src)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        out = tmpdir / "icon.svg.png"
        if not out.exists():
            raise RuntimeError(f"qlmanage gav ingen PNG for {variant}")
        im = Image.open(out).convert("RGBA").crop((0, 0, _MASTER_PX, _MASTER_PX))

        # Vagt mod netop den tomme-thumbnail-fejl: midten SKAL være brandgrøn.
        # Uden den her committer man et hvidt firkantet ikon uden at opdage det.
        r, g, b = im.convert("RGB").getpixel((_MASTER_PX // 2, 4))  # type: ignore[misc]
        mid = (r, g, b)
        if mid != _GREEN_RGB:
            raise RuntimeError(
                f"{variant}: forventede {_GREEN_RGB} i toppen, fik {mid} - "
                "qlmanage har sandsynligvis lavet et tomt thumbnail."
            )
        return im


_masters: dict[str, Image.Image] = {}


def render(variant: str, px: int) -> Image.Image:
    """Ikon i ønsket størrelse, nedskaleret fra master-rasteriseringen."""
    if variant not in _masters:
        _masters[variant] = _rasterize(variant)
    im = _masters[variant]
    if px != _MASTER_PX:
        im = im.resize((px, px), Image.Resampling.LANCZOS)
    else:
        im = im.copy()
    if variant == "rounded":
        im.putalpha(rounded_alpha(px))
    return im


def write_ico(path: Path, frames: list[Image.Image]) -> None:
    """Skriv en multi-størrelses .ico med ÉN native rendering pr. størrelse.

    Pillows egen ICO-writer duer ikke her: den ignorerer append_images og
    nedskalerer i stedet basisbilledet, så en 16 px-basis giver en fil med kun
    16x16 (målt: 1 ramme, 191 bytes). Og at nedskalere 48 -> 16 gør de tynde
    hvide streger grå og grødede - netop ved den størrelse, faneblade bruger.
    Rammerne lægges derfor ind som PNG, hvilket ICO har understøttet siden
    Vista og alle browsere læser.
    """
    payloads = []
    for im in frames:
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        payloads.append(buf.getvalue())

    offset = 6 + 16 * len(payloads)
    header = struct.pack("<HHH", 0, 1, len(payloads))   # reserved, type=icon, antal
    entries, blob = b"", b""
    for im, data in zip(frames, payloads):
        # 0 betyder 256 i ICO-formatet; vores største ramme er 48, så det
        # rammer vi aldrig, men konverteringen er gratis at have med.
        w = 0 if im.width >= 256 else im.width
        h = 0 if im.height >= 256 else im.height
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset)
        blob += data
        offset += len(data)
    path.write_bytes(header + entries + blob)


def main() -> int:
    if not shutil.which("qlmanage"):
        print("fejl: qlmanage findes kun på macOS - kør scriptet lokalt.")
        return 1

    ico_sizes = [16, 32, 48]
    ico_path = STATIC / "favicon.ico"
    write_ico(ico_path, [render("rounded", s) for s in ico_sizes])
    print(f"  {ico_path.relative_to(ROOT)} ({', '.join(f'{s}x{s}' for s in ico_sizes)})")

    for name, variant, px in (
        ("icon-192.png", "rounded", 192),
        ("icon-512.png", "rounded", 512),
        ("icon-maskable-512.png", "maskable", 512),
        ("apple-touch-icon.png", "fullbleed", 180),
    ):
        path = STATIC / name
        render(variant, px).save(path, format="PNG", optimize=True)
        print(f"  {path.relative_to(ROOT)} ({px}x{px}, {variant})")

    print("Færdig. Husk at bumpe ?v= i templates/base.html hvis ikonerne er ændret.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
