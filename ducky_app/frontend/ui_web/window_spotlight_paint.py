"""Pixels for the desktop spotlight, drawn to match the in-app Show me popup.

The in-app spotlight (``showme/showme.css``) is a dimmed page with a rounded hole,
a glowing ring and a popup card. UEFN and other programs cannot host that CSS, so
this module paints the same three pieces with PIL and shows each one through a
per-pixel layered window (``UpdateLayeredWindow``). Colours, radii, spacing, fonts
and the close / Back / Next buttons follow the stylesheet's defaults; the shadow
and the ring's glow are blurred the same way the CSS blurs them.

Everything that touches a window handle acts on the real top-level window
(``GetAncestor(..., GA_ROOT)``): a Tk Toplevel's ``winfo_id`` is the child window
inside it, and styles or z-order applied there never reach the screen.

Linux X11 has no per-pixel layered windows without a compositor-aware visual, so
``LayeredWindow`` there shows the picture on an override-redirect Tk window cut to
its visible pixels with XShape (``alpha_rects``); partial alpha such as the dim
becomes window opacity, and only when a compositor is running.
"""

from __future__ import annotations

import ctypes
import re
import sys
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from frontend import platform_info

# showme.css defaults (the dark theme); hex values taken from the stylesheet.
ACCENT = (76, 141, 255)  # #4c8dff
SURFACE = (28, 31, 38)  # #1c1f26
TEXT = (240, 242, 246)  # #f0f2f6
TEXT_DIM = (197, 204, 216)  # #c5ccd8
TEXT_MUTED = (154, 163, 178)  # #9aa3b2
DIM_ALPHA = 140  # rgba(0, 0, 0, 0.55)
CARD_W = 360  # .showme-popup width
SHADOW_PAD = (40, 40, 40, 64)  # left, top, right, bottom: room for "0 14px 44px rgba(0,0,0,.5)"
RING_PAD = 24  # room for the ring's halo and glow
_SS = 4  # supersampling for anti-aliased shapes

_HIT_CLOSE = "close"
_HIT_NEXT = "next"
_HIT_BACK = "back"


def mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    """``color-mix(in srgb, b t, a 1-t)`` for opaque colours."""
    t = min(1.0, max(0.0, float(t)))
    return tuple(int(round(a[i] * (1 - t) + b[i] * t)) for i in range(3))  # type: ignore[return-value]


def over(base: tuple[int, int, int], rgba: tuple[int, int, int, float]) -> tuple[int, int, int]:
    """``rgba`` (alpha 0..1) composited over an opaque ``base``."""
    r, g, b, a = rgba
    return mix(base, (r, g, b), a)


# rgba(255,255,255,.14) and .12 and .06 on the surface, as the stylesheet layers them.
BORDER = over(SURFACE, (255, 255, 255, 0.14))
DIVIDER = over(SURFACE, (255, 255, 255, 0.12))
BTN_BG = over(SURFACE, (255, 255, 255, 0.06))
BTN_BG_HOVER = over(SURFACE, (255, 255, 255, 0.12))
# color-mix(in srgb, accent 45%, rgba(255,255,255,.14)) then over the surface.
CARD_BORDER = over(SURFACE, (*mix((255, 255, 255), ACCENT, 0.45 / (0.45 + 0.55 * 0.14) * 1.0), 0.45 + 0.55 * 0.14))
PRIMARY_BORDER = over(SURFACE, (*ACCENT, 0.70))
PRIMARY_BG = over(SURFACE, (*ACCENT, 0.30))
PRIMARY_BG_HOVER = over(SURFACE, (*ACCENT, 0.42))

_fonts: dict[tuple[str, int], Any] = {}


def font(weight: str, px: float) -> Any:
    """Segoe UI at ``px`` pixels: regular, semibold (CSS 600-650) or bold. Cached."""
    size = max(6, int(round(px)))
    key = (weight, size)
    hit = _fonts.get(key)
    if hit is not None:
        return hit
    names = {
        "regular": ("segoeui.ttf",),
        "semibold": ("seguisb.ttf", "segoeuib.ttf", "segoeui.ttf"),
        "bold": ("segoeuib.ttf", "seguisb.ttf", "segoeui.ttf"),
    }[weight]
    loaded = None
    for name in names:
        try:
            loaded = ImageFont.truetype(name, size)
            break
        except OSError:
            continue
    if loaded is None:
        try:
            loaded = ImageFont.load_default(size=size)
        except TypeError:  # older PIL: no size argument
            loaded = ImageFont.load_default()
    _fonts[key] = loaded
    return loaded


def plain_text(markdown: str) -> list[str]:
    """Paragraphs of plain text from the light markdown the agent writes."""
    text = str(markdown or "").replace("\r\n", "\n")
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"(?m)^\s*[-*]\s+", "• ", text)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text)]
    return [re.sub(r"\s*\n\s*", " ", p) for p in paragraphs if p]


def wrap(text: str, fnt: Any, max_w: float) -> list[str]:
    """Greedy word wrap by measured width; a word wider than the line is cut."""
    lines: list[str] = []
    for raw_line in str(text).split("\n"):
        words = raw_line.split(" ")
        line = ""
        for word in words:
            trial = word if not line else line + " " + word
            if fnt.getlength(trial) <= max_w or not line:
                if fnt.getlength(trial) > max_w and not line:
                    # One word wider than the card: split it by characters.
                    piece = ""
                    for ch in word:
                        if fnt.getlength(piece + ch) > max_w and piece:
                            lines.append(piece)
                            piece = ch
                        else:
                            piece += ch
                    line = piece
                    continue
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def rounded(
    w: int,
    h: int,
    radius: float,
    fill: tuple[int, int, int, int] | None,
    border: tuple[int, int, int, int] | None = None,
    border_w: float = 1.0,
) -> Image.Image:
    """An anti-aliased rounded rectangle (supersampled masks), RGBA."""
    w, h = max(int(w), 1), max(int(h), 1)
    big = (w * _SS, h * _SS)
    r = max(0.0, float(radius)) * _SS
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if fill is not None:
        mask = Image.new("L", big, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, big[0] - 1, big[1] - 1), radius=r, fill=255)
        mask = mask.resize((w, h), Image.LANCZOS)
        layer = Image.new("RGBA", (w, h), fill)
        layer.putalpha(Image.eval(mask, lambda v: v * fill[3] // 255))
        out = Image.alpha_composite(out, layer)
    if border is not None and border_w > 0:
        bw = max(1, int(round(border_w * _SS)))
        mask = Image.new("L", big, 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((0, 0, big[0] - 1, big[1] - 1), radius=r, fill=255)
        inner_r = max(0.0, r - bw)
        draw.rounded_rectangle((bw, bw, big[0] - 1 - bw, big[1] - 1 - bw), radius=inner_r, fill=0)
        mask = mask.resize((w, h), Image.LANCZOS)
        layer = Image.new("RGBA", (w, h), border)
        layer.putalpha(Image.eval(mask, lambda v: v * border[3] // 255))
        out = Image.alpha_composite(out, layer)
    return out


def _sparkle(size: int, color: tuple[int, int, int]) -> Image.Image:
    """The Sparkles icon: a four-point star with curved sides, like the SVG path."""
    n = max(int(size), 4)
    big = n * _SS
    mask = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(mask)
    c = big / 2
    waist = big * 0.11
    pts = [
        (c, 0), (c + waist, c - waist), (big, c), (c + waist, c + waist),
        (c, big), (c - waist, c + waist), (0, c), (c - waist, c - waist),
    ]
    draw.polygon(pts, fill=255)
    # Round the waist a little so the arms curve instead of pinching.
    draw.ellipse((c - waist * 1.4, c - waist * 1.4, c + waist * 1.4, c + waist * 1.4), fill=255)
    mask = mask.resize((n, n), Image.LANCZOS)
    icon = Image.new("RGBA", (n, n), (*color, 255))
    icon.putalpha(mask)
    return icon


def _cross(size: int, color: tuple[int, int, int], stroke: float) -> Image.Image:
    """The Close icon: two strokes from corner to corner, 2px wide on a 24px grid."""
    n = max(int(size), 4)
    big = n * _SS
    mask = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(mask)
    inset = big * 6 / 24
    width = max(1, int(round(stroke * _SS)))
    draw.line((inset, inset, big - inset, big - inset), fill=255, width=width)
    draw.line((big - inset, inset, inset, big - inset), fill=255, width=width)
    mask = mask.resize((n, n), Image.LANCZOS)
    icon = Image.new("RGBA", (n, n), (*color, 255))
    icon.putalpha(mask)
    return icon


@dataclass
class CardState:
    title: str = ""
    body: str = ""
    index: int = 0
    total: int = 1
    click: bool = False
    hover: str | None = None
    pressed: str | None = None


@dataclass
class CardPixels:
    image: Image.Image
    pad: tuple[int, int, int, int]
    """Transparent shadow margin (left, top, right, bottom) around the visible card."""
    visible: tuple[int, int]
    """Visible card size inside the margin."""
    hits: dict[str, tuple[int, int, int, int]]
    """Button rectangles in image pixels: close, next, back."""

    def hit(self, x: int, y: int) -> str | None:
        for name, (x0, y0, x1, y1) in self.hits.items():
            if x0 <= x < x1 and y0 <= y < y1:
                return name
        return None


def _button(label: str, fnt: Any, s: float, *, primary: bool, hover: bool) -> tuple[Image.Image, int, int]:
    pad_x, pad_y = 12 * s, 5 * s
    tw = fnt.getlength(label)
    ascent, descent = fnt.getmetrics()
    th = ascent + descent
    w = int(round(tw + pad_x * 2 + 2))
    h = int(round(th + pad_y * 2 + 2))
    if primary:
        bg = PRIMARY_BG_HOVER if hover else PRIMARY_BG
        border = PRIMARY_BORDER
    else:
        bg = BTN_BG_HOVER if hover else BTN_BG
        border = BORDER
    img = rounded(w, h, 8 * s, (*bg, 255), (*border, 255), 1 * s)
    ImageDraw.Draw(img).text((pad_x + 1, pad_y + 1), label, font=fnt, fill=TEXT)
    return img, w, h


_shadows: dict[tuple[int, int, int, int, float], Image.Image] = {}


def _shadow(size: tuple[int, int], card_w: int, card_h: int, at: tuple[int, int], s: float) -> Image.Image:
    """0 14px 44px rgba(0,0,0,.5): a blurred black card (sigma 22px). Cached per size."""
    key = (size[0], size[1], card_w, card_h, round(s, 3))
    hit = _shadows.get(key)
    if hit is not None:
        return hit
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    shadow.paste(rounded(card_w, card_h, 12 * s, (0, 0, 0, 128)), (at[0], at[1] + int(round(14 * s))))
    shadow = shadow.filter(ImageFilter.GaussianBlur(22 * s))
    # Any pixel with alpha > 0 takes the click, so the faint outer tail of the blur
    # is cut to fully transparent: the window stays clickable only where it is seen.
    shadow.putalpha(Image.eval(shadow.getchannel("A"), lambda v: 0 if v < 6 else v))
    if len(_shadows) > 16:
        _shadows.clear()
    _shadows[key] = shadow
    return shadow


def render_card(state: CardState, scale: float = 1.0) -> CardPixels:
    """The popup card at ``scale`` (1.0 = 96 dpi), with its drop shadow baked in."""
    s = max(0.5, float(scale))
    pad_l, pad_t, pad_r, pad_b = (int(round(v * s)) for v in SHADOW_PAD)
    card_w = int(round(CARD_W * s))
    inner_x = int(round(14 * s))
    inner_w = card_w - inner_x * 2
    title_font = font("semibold", 15 * s)
    body_font = font("regular", 13 * s)
    small_font = font("regular", 12 * s)
    btn_font = font("semibold", 12.5 * s)

    close_size = int(round(28 * s))
    icon = int(round(16 * s))
    gap = int(round(8 * s))

    # Head: icon, title, close button. The title wraps beside both.
    title_w = inner_w - icon - gap - close_size - int(round(2 * s))
    title_lines = wrap(state.title or "", title_font, title_w)
    t_asc, t_desc = title_font.getmetrics()
    title_lh = int(round(15 * 1.3 * s))
    title_h = max(title_lh * len(title_lines), close_size - int(round(4 * s)))
    head_h = title_h

    # Body paragraphs.
    b_asc, b_desc = body_font.getmetrics()
    body_lh = int(round(13 * 1.5 * s))
    paragraphs = [wrap(p, body_font, inner_w) for p in plain_text(state.body)]
    body_h = 0
    for i, lines in enumerate(paragraphs):
        body_h += body_lh * len(lines) + (int(round(8 * s)) if i < len(paragraphs) - 1 else 0)
    body_h = min(body_h, int(round(340 * s)))

    many = state.total > 1
    last = state.index >= state.total - 1
    buttons: list[tuple[str, Image.Image, int, int]] = []
    if many:
        if state.index > 0:
            img, w, h = _button("Back", btn_font, s, primary=False, hover=state.hover == _HIT_BACK)
            buttons.append((_HIT_BACK, img, w, h))
        if last:
            img, w, h = _button("Close", btn_font, s, primary=True, hover=state.hover == _HIT_NEXT)
            buttons.append((_HIT_NEXT, img, w, h))
        elif not state.click:
            img, w, h = _button("Next", btn_font, s, primary=True, hover=state.hover == _HIT_NEXT)
            buttons.append((_HIT_NEXT, img, w, h))
    steps_h = 0
    if many:
        row_h = max([h for _n, _i, _w, h in buttons] + [int(round(12 * 1.5 * s))])
        steps_h = int(round(10 * s)) + 1 + int(round(10 * s)) + row_h

    card_h = int(round(12 * s)) + head_h + int(round(6 * s)) + body_h + steps_h + int(round(12 * s))
    image = Image.new("RGBA", (pad_l + card_w + pad_r, pad_t + card_h + pad_b), (0, 0, 0, 0))

    image = Image.alpha_composite(image, _shadow(image.size, card_w, card_h, (pad_l, pad_t), s))

    card = rounded(card_w, card_h, 12 * s, (*SURFACE, 255), (*CARD_BORDER, 255), 1 * s)
    draw = ImageDraw.Draw(card)
    x = inner_x
    y = int(round(12 * s))
    card.alpha_composite(_sparkle(icon, ACCENT), (x, y + int(round(2 * s))))
    tx = x + icon + gap
    ty = y
    for line in title_lines:
        draw.text((tx, ty + (title_lh - (t_asc + t_desc)) // 2), line, font=title_font, fill=TEXT)
        ty += title_lh

    # Close: 28px square, 1px border, radius 8, pulled 4px up and 6px right of the padding box.
    cx = card_w - inner_x + int(round(6 * s)) - close_size
    cy = y - int(round(4 * s))
    close_hover = state.hover == _HIT_CLOSE
    close_img = rounded(close_size, close_size, 8 * s, (*(BTN_BG_HOVER if close_hover else BTN_BG), 255), (*BORDER, 255), 1 * s)
    cross = _cross(int(round(15 * s)), TEXT, 2 * s * 15 / 24)
    close_img.alpha_composite(cross, ((close_size - cross.width) // 2, (close_size - cross.height) // 2))
    card.alpha_composite(close_img, (cx, cy))
    hits: dict[str, tuple[int, int, int, int]] = {
        _HIT_CLOSE: (pad_l + cx, pad_t + cy, pad_l + cx + close_size, pad_t + cy + close_size)
    }

    y += head_h + int(round(6 * s))
    body_top = y
    for i, lines in enumerate(paragraphs):
        for line in lines:
            if y + body_lh > body_top + body_h:
                break
            draw.text((x, y + (body_lh - (b_asc + b_desc)) // 2), line, font=body_font, fill=TEXT_DIM)
            y += body_lh
        if i < len(paragraphs) - 1:
            y += int(round(8 * s))
    y = body_top + body_h

    if many:
        y += int(round(10 * s))
        draw.line((x, y, x + inner_w, y), fill=DIVIDER, width=max(1, int(round(s))))
        y += 1 + int(round(10 * s))
        row_h = steps_h - int(round(10 * s)) - 1 - int(round(10 * s))
        count = f"{state.index + 1} / {state.total}"
        s_asc, s_desc = small_font.getmetrics()
        text_y = y + (row_h - (s_asc + s_desc)) // 2
        draw.text((x, text_y), count, font=small_font, fill=TEXT_MUTED)
        if state.click and not last:
            hint_x = x + small_font.getlength(count) + int(round(8 * s))
            draw.text((hint_x, text_y), "Click the highlight to go on", font=small_font, fill=ACCENT)
        bx = x + inner_w
        for name, img, w, h in reversed(buttons):
            bx -= w
            by = y + (row_h - h) // 2
            card.alpha_composite(img, (bx, by))
            hits[name] = (pad_l + bx, pad_t + by, pad_l + bx + w, pad_t + by + h)
            bx -= int(round(6 * s))

    image.alpha_composite(card, (pad_l, pad_t))
    return CardPixels(image=image, pad=(pad_l, pad_t, pad_r, pad_b), visible=(card_w, card_h), hits=hits)


def clear_rect(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """``image`` with the alpha of ``box`` (x0, y0, x1, y1, image pixels) set to 0, so clicks pass."""
    x0, y0, x1, y1 = (int(v) for v in box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(image.width, x1), min(image.height, y1)
    if x1 <= x0 or y1 <= y0:
        return image
    out = image.copy()
    alpha = out.getchannel("A")
    alpha.paste(0, (x0, y0, x1, y1))
    out.putalpha(alpha)
    return out


def render_ring(w: int, h: int, radius: int, scale: float = 1.0) -> tuple[Image.Image, int]:
    """The ring around the hole with its white halo and blue glow. Returns (image, margin)."""
    s = max(0.5, float(scale))
    m = int(round(RING_PAD * s))
    w, h = max(int(w), 4), max(int(h), 4)
    r = float(radius)
    image = Image.new("RGBA", (w + 2 * m, h + 2 * m), (0, 0, 0, 0))
    # Glow: 0 0 18px 4px accent 55% → spread 4, blur sigma 9.
    spread = int(round(4 * s))
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow.paste(rounded(w + 2 * spread, h + 2 * spread, r + spread, (*ACCENT, 140)), (m - spread, m - spread))
    glow = glow.filter(ImageFilter.GaussianBlur(9 * s))
    # A box-shadow never paints under its own box: keep the glow outside the ring only.
    inside = Image.new("L", image.size, 0)
    inside.paste(rounded(w, h, r, (0, 0, 0, 255)).getchannel("A"), (m, m))
    from PIL import ImageChops

    glow.putalpha(ImageChops.multiply(glow.getchannel("A"), ImageChops.invert(inside)))
    image = Image.alpha_composite(image, glow)
    # Halo: 0 0 0 2px white 22%.
    halo_w = int(round(2 * s))
    halo = rounded(w + 2 * halo_w, h + 2 * halo_w, r + halo_w, None, (255, 255, 255, 56), halo_w)
    image.alpha_composite(halo, (m - halo_w, m - halo_w))
    # Border: 2px solid accent, inside the box like a CSS border.
    image.alpha_composite(rounded(w, h, r, None, (*ACCENT, 255), 2 * s), (m, m))
    return image, m


def render_dim(vw: int, vh: int, hole: tuple[int, int, int, int], radius: int) -> Image.Image:
    """The dimmed screen with a fully transparent rounded hole (clicks pass through it)."""
    vw, vh = max(int(vw), 1), max(int(vh), 1)
    alpha = Image.new("L", (vw, vh), DIM_ALPHA)
    hx, hy, hw, hh = (int(v) for v in hole)
    if hw > 0 and hh > 0:
        patch = rounded(hw, hh, radius, (0, 0, 0, 255)).getchannel("A")
        cut = Image.eval(patch, lambda v: DIM_ALPHA * (255 - v) // 255)
        alpha.paste(cut, (hx, hy))
    image = Image.new("RGBA", (vw, vh), (0, 0, 0, 255))
    image.putalpha(alpha)
    return image


def _bgra_premultiplied(image: Image.Image) -> bytes:
    """Top-down BGRA with premultiplied alpha, as a 32-bit DIB wants it."""
    rgba = image.convert("RGBA")
    r, g, b, a = rgba.split()
    from PIL import ImageChops

    r = ImageChops.multiply(r, a)
    g = ImageChops.multiply(g, a)
    b = ImageChops.multiply(b, a)
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


def _spans(row: bytes) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    i = 0
    while True:
        start = row.find(b"\xff", i)
        if start < 0:
            return out
        end = row.find(b"\x00", start)
        if end < 0:
            end = len(row)
        out.append((start, end))
        i = end


def alpha_rects(alpha: Image.Image, threshold: int) -> list[tuple[int, int, int, int]]:
    """The pixels with alpha >= ``threshold`` as (x, y, w, h) rectangles, for an XShape region.

    Identical rows merge into one taller rectangle, so a full-screen dim with one hole is a
    handful of rectangles, not a million pixels.
    """
    w, h = alpha.size
    data = alpha.point(lambda a: 255 if a >= threshold else 0).tobytes()
    rects: list[tuple[int, int, int, int]] = []
    prev: bytes | None = None
    start_y = 0
    for y in range(h + 1):
        row = data[y * w:(y + 1) * w] if y < h else None
        if row is not None and row == prev:
            continue
        if prev is not None:
            rects.extend((x0, start_y, x1 - x0, y - start_y) for x0, x1 in _spans(prev))
        prev, start_y = row, y
    return rects


def x11_composited(display: Any) -> bool:
    """A compositing manager owns ``_NET_WM_CM_Sn``: window opacity will show."""
    from Xlib import X

    sel = display.intern_atom(f"_NET_WM_CM_S{display.get_default_screen()}")
    return display.get_selection_owner(sel) != X.NONE


def x11_layer(image: Image.Image, composited: bool) -> tuple[Image.Image, float, list[tuple[int, int, int, int]]]:
    """What an X11 overlay shows for ``image``: opaque RGB, window opacity, shape rectangles.

    The most opaque pixel sets the window opacity (the dim is 0.55 black, the card 1.0) and the
    shape keeps pixels at least half that opaque. Without a compositor opacity cannot show, so a
    see-through layer (the dim) shows nothing rather than blacking out the screen.
    """
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    peak = alpha.getextrema()[1]
    rgb = Image.new("RGB", rgba.size, (0, 0, 0))
    if peak == 0 or (peak < 250 and not composited):
        return rgb, 1.0, []
    rgb.paste(rgba.convert("RGB"), (0, 0), alpha.point(lambda a: min(255, a * 255 // peak)))
    return rgb, peak / 255.0, alpha_rects(alpha, max(1, peak // 2))


# ---- Windows plumbing -----------------------------------------------------------

_GA_ROOT = 2
_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_LAYERED = 0x00080000
_WS_EX_NOACTIVATE = 0x08000000
_HWND_TOPMOST = -1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040
_ULW_ALPHA = 0x00000002
_AC_SRC_OVER = 0x00
_AC_SRC_ALPHA = 0x01


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 1)]


def _user32() -> Any:
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    u.GetAncestor.restype = ctypes.c_void_p
    u.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u.SetWindowPos.restype = ctypes.c_int
    u.GetDC.argtypes = [ctypes.c_void_p]
    u.GetDC.restype = ctypes.c_void_p
    u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    u.UpdateLayeredWindow.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_POINT), ctypes.POINTER(_SIZE), ctypes.c_void_p,
        ctypes.POINTER(_POINT), ctypes.c_uint32, ctypes.POINTER(_BLENDFUNCTION), ctypes.c_uint32,
    ]
    u.UpdateLayeredWindow.restype = ctypes.c_int
    return u


def _gdi32() -> Any:
    g = ctypes.WinDLL("gdi32", use_last_error=True)
    g.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    g.CreateCompatibleDC.restype = ctypes.c_void_p
    g.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.POINTER(_BITMAPINFO), ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_uint32]
    g.CreateDIBSection.restype = ctypes.c_void_p
    g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    g.SelectObject.restype = ctypes.c_void_p
    g.DeleteObject.argtypes = [ctypes.c_void_p]
    g.DeleteDC.argtypes = [ctypes.c_void_p]
    return g


def root_hwnd(widget: Any) -> int:
    """The real top-level window of a Tk widget (``winfo_id`` is the child inside it)."""
    child = int(widget.winfo_id())
    if sys.platform != "win32":
        return child
    root = _user32().GetAncestor(child, _GA_ROOT)
    return int(root or child)


def dpi_scale(hwnd: int) -> float:
    """Monitor scale of a window (1.0 at 96 dpi)."""
    if sys.platform != "win32":
        return 1.0
    try:
        user = ctypes.WinDLL("user32")
        get = getattr(user, "GetDpiForWindow", None)
        if get is None:
            return 1.0
        get.argtypes = [ctypes.c_void_p]
        get.restype = ctypes.c_uint
        dpi = int(get(int(hwnd)) or 0)
        return dpi / 96.0 if dpi > 0 else 1.0
    except Exception:
        return 1.0


class LayeredWindow:
    """A Tk Toplevel shown through ``UpdateLayeredWindow``: shape and alpha come from the bitmap.

    Fully transparent pixels let clicks through to whatever is behind; the rest of the
    window takes the click (Tk sees it as an event on ``top``). ``click_through`` makes
    every pixel pass clicks, for the ring.
    """

    def __init__(self, root: Any, *, click_through: bool = False) -> None:
        import tkinter as tk

        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.geometry("1x1+0+0")
        self.top.update_idletasks()
        self.hwnd = root_hwnd(self.top)
        self.x = 0
        self.y = 0
        self.size = (1, 1)
        self._painted = False
        self._click_through = click_through
        self._photo: Any = None
        self._label: Any = None
        if platform_info.IS_LINUX:
            # Override-redirect Toplevel: the mapped X window is its wrapper, not winfo_id.
            self.hwnd = int(self.top.wm_frame(), 16)
        if sys.platform == "win32":
            user = _user32()
            style = int(user.GetWindowLongPtrW(self.hwnd, _GWL_EXSTYLE))
            style |= _WS_EX_LAYERED | _WS_EX_NOACTIVATE
            if click_through:
                style |= _WS_EX_TRANSPARENT
            user.SetWindowLongPtrW(self.hwnd, _GWL_EXSTYLE, style)

    def paint(self, image: Image.Image, x: int, y: int) -> None:
        """Show ``image`` with its top-left at screen ``x, y``."""
        w, h = image.size
        self.x, self.y, self.size = int(x), int(y), (int(w), int(h))
        self.top.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
        self.top.update_idletasks()
        if platform_info.IS_LINUX:
            self._paint_x11(image)
            return
        if sys.platform != "win32":
            return
        user, gdi = _user32(), _gdi32()
        data = _bgra_premultiplied(image)
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        screen = user.GetDC(None)
        mem = gdi.CreateCompatibleDC(screen)
        bitmap = gdi.CreateDIBSection(screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        try:
            if not bitmap or not bits:
                raise OSError("CreateDIBSection failed")
            ctypes.memmove(bits, data, len(data))
            old = gdi.SelectObject(mem, bitmap)
            blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)
            ok = user.UpdateLayeredWindow(
                self.hwnd, screen, ctypes.byref(_POINT(int(x), int(y))), ctypes.byref(_SIZE(w, h)),
                mem, ctypes.byref(_POINT(0, 0)), 0, ctypes.byref(blend), _ULW_ALPHA,
            )
            gdi.SelectObject(mem, old)
            if not ok:
                raise OSError(f"UpdateLayeredWindow failed: {ctypes.get_last_error()}")
            self._painted = True
        finally:
            if bitmap:
                gdi.DeleteObject(bitmap)
            gdi.DeleteDC(mem)
            user.ReleaseDC(None, screen)

    def _paint_x11(self, image: Image.Image) -> None:
        import base64
        import io
        import tkinter as tk

        from Xlib import X
        from Xlib.ext import shape

        from frontend.ui_web.window_spotlight import _xd

        d = _xd()
        rgb, opacity, rects = x11_layer(image, x11_composited(d))
        buf = io.BytesIO()
        rgb.save(buf, "PNG")
        self._photo = tk.PhotoImage(master=self.top, data=base64.b64encode(buf.getvalue()))
        if self._label is None:
            # Child of the Toplevel: the Toplevel's own bindings still fire (bindtags).
            self._label = tk.Label(self.top, bd=0, highlightthickness=0, padx=0, pady=0)
            self._label.place(x=0, y=0, relwidth=1, relheight=1)
        self._label.configure(image=self._photo)
        try:
            self.top.attributes("-alpha", opacity)
        except Exception:
            pass
        self.top.update_idletasks()
        win = d.create_resource_object("window", self.hwnd)
        win.shape_rectangles(shape.SO.Set, shape.SK.Bounding, X.Unsorted, 0, 0, rects)
        if self._click_through:
            win.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, [])
        d.flush()
        self._painted = True

    def move(self, x: int, y: int) -> None:
        if (int(x), int(y)) == (self.x, self.y):
            return
        self.x, self.y = int(x), int(y)
        self.top.geometry(f"+{int(x)}+{int(y)}")
        if sys.platform == "win32":
            _user32().SetWindowPos(self.hwnd, _HWND_TOPMOST, int(x), int(y), 0, 0, _SWP_NOSIZE | _SWP_NOACTIVATE)

    def raise_(self) -> None:
        """To the top of the topmost band. Call for the dim, then the ring, then the card."""
        if platform_info.IS_LINUX:
            self.top.lift()
        if sys.platform == "win32":
            _user32().SetWindowPos(self.hwnd, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_SHOWWINDOW)

    def show(self, visible: bool) -> None:
        if visible:
            self.top.deiconify()
        else:
            self.top.withdraw()

    def bind(self, sequence: str, func: Any) -> None:
        self.top.bind(sequence, func)

    def set_cursor(self, cursor: str) -> None:
        try:
            self.top.configure(cursor=cursor)
        except Exception:
            pass

    def destroy(self) -> None:
        try:
            self.top.destroy()
        except Exception:
            pass
