"""PDF report generator for tennis analysis — adapted for bot use."""

import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from config import PDF_DIR

W, H = A4
NAVY = HexColor('#1B2A4A')
BLUE = HexColor('#2E5090')
LBLUE = HexColor('#D6E4F0')
GOLD = HexColor('#D4A843')
GREEN = HexColor('#2E7D32')
WHITE = HexColor('#FFFFFF')
BLACK = HexColor('#000000')
LGRAY = HexColor('#F5F5F5')
GRAY = HexColor('#666666')
RED = HexColor('#CC3333')
ORANGE = HexColor('#E67E22')

LM = 20 * mm
CW = W - 2 * LM
TLM = LM + 3 * mm

# ── Font setup: auto-download DejaVu if not found ──
import urllib.request
import zipfile

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')

def _ensure_dejavu_fonts():
    """Download DejaVu Sans fonts if not available locally."""
    os.makedirs(FONTS_DIR, exist_ok=True)
    regular = os.path.join(FONTS_DIR, 'DejaVuSans.ttf')
    bold = os.path.join(FONTS_DIR, 'DejaVuSans-Bold.ttf')

    if os.path.exists(regular) and os.path.exists(bold):
        return FONTS_DIR

    # Check system paths first
    for fd in ['/usr/share/fonts/truetype/dejavu/',
               '/usr/share/fonts/dejavu/']:
        if os.path.exists(fd + 'DejaVuSans.ttf'):
            return fd

    # Download from GitHub mirror
    print("Downloading DejaVu fonts for Cyrillic PDF support...")
    url = "https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.zip"
    zip_path = os.path.join(FONTS_DIR, 'dejavu.zip')
    try:
        urllib.request.urlretrieve(url, zip_path)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                if name.endswith('DejaVuSans.ttf') or name.endswith('DejaVuSans-Bold.ttf'):
                    data = zf.read(name)
                    fname = os.path.basename(name)
                    with open(os.path.join(FONTS_DIR, fname), 'wb') as f:
                        f.write(data)
        os.remove(zip_path)
        print(f"Fonts installed to {FONTS_DIR}")
    except Exception as e:
        print(f"Font download failed: {e}")
    return FONTS_DIR

_font_dir = _ensure_dejavu_fonts()
try:
    pdfmetrics.registerFont(TTFont('DJS', os.path.join(_font_dir, 'DejaVuSans.ttf')))
    pdfmetrics.registerFont(TTFont('DJSB', os.path.join(_font_dir, 'DejaVuSans-Bold.ttf')))
    DJS = 'DJS'
    DJSB = 'DJSB'
except Exception:
    print("WARNING: DejaVu fonts not available, Cyrillic will not render in PDFs")
    DJS = 'Helvetica'
    DJSB = 'Helvetica-Bold'


import re as _re
_EMOJI_RE = _re.compile(
    "["
    "\U0001F300-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F000-\U0001F2FF"
    "]+",
    flags=_re.UNICODE,
)

def _strip_emoji(text):
    """Remove emoji glyphs that DejaVu cannot render (appears as boxes)."""
    if not isinstance(text, str):
        text = str(text)
    return _EMOJI_RE.sub("", text).strip()


def _to_text(value, max_items=4):
    """
    Normalize any value (dict, list, None, str, number) into a readable plain-text
    string suitable for drawing in PDF. Fixes the bug where raw JSON like
    {'total': '...', 'recent_matches': [...]} was shown verbatim in H2H/map_veto/player_status.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = []
        for item in value[:max_items]:
            t = _to_text(item, max_items=max_items)
            if t:
                parts.append(t)
        return "; ".join(parts)
    if isinstance(value, dict):
        # Common patterns we know about
        if "total" in value or "overall" in value:
            head = str(value.get("total") or value.get("overall") or "")
            extras = []
            # surfaces nesting: {"grass": "...", "hard": "..."}
            if isinstance(value.get("surfaces"), dict):
                for k, v in list(value["surfaces"].items())[:3]:
                    extras.append(f"{k}: {_to_text(v)}")
            # recent_matches list
            rm = value.get("recent_matches") or value.get("recent")
            if isinstance(rm, list) and rm:
                last = rm[0]
                if isinstance(last, dict):
                    extras.append(f"last: {last.get('event','')} {last.get('result','')}".strip())
            return head + (" | " + " · ".join(extras) if extras else "")
        # generic dict — render key:value pairs
        parts = []
        for k, v in list(value.items())[:max_items]:
            parts.append(f"{k}: {_to_text(v)}")
        return " | ".join(parts)
    return str(value)


def _wrap(c, text, font, size, max_w):
    """Word-wrap text into lines."""
    text = _strip_emoji(_to_text(text))
    words = text.split()
    lines, line = [], ""
    for w in words:
        test = f"{line} {w}" if line else w
        if c.stringWidth(test, font, size) < max_w:
            line = test
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def _fit(c, text, font, size, max_w):
    """
    Truncate text to fit width with an ellipsis. Replaces blind [:N] slicing
    that caused mid-word breaks like "...на другом покрытии (гл".
    """
    text = _strip_emoji(_to_text(text))
    if c.stringWidth(text, font, size) <= max_w:
        return text
    ell = "…"
    while text and c.stringWidth(text + ell, font, size) > max_w:
        text = text[:-1]
    return (text.rstrip() + ell) if text else ell


def _sbar(c, title, y, gap=3 * mm):
    """Section bar."""
    y -= gap
    h = 5.5 * mm
    c.setFillColor(BLUE)
    c.rect(LM, y - h, CW, h, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont(DJSB, 7.5)
    c.drawString(TLM, y - h + 1.5 * mm, _strip_emoji(_to_text(title)))
    return y - h - 1 * mm


_SPORT_FOOTER = {
    "tennis": "Tennis Analyst | Исследовательский анализ | Не является рекомендацией по ставкам",
    "cs2":    "CS2 Analyst | Исследовательский анализ | Не является рекомендацией по ставкам",
    "dota2":  "Dota 2 Analyst | Исследовательский анализ | Не является рекомендацией по ставкам",
}

def _footer(c, sport: str = "tennis"):
    """Page footer. Sport-specific branding; defaults to tennis."""
    fh = 4.5 * mm
    fy = 10 * mm
    c.setFillColor(NAVY)
    c.rect(LM, fy, CW, fh, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont(DJS, 5.5)
    text = _SPORT_FOOTER.get(sport, _SPORT_FOOTER["tennis"])
    c.drawString(TLM, fy + 1.2 * mm, text)


def _ensure_tennis_distribution(dist: dict, p: float, bo: int) -> dict:
    """
    Always RECOMPUTE distribution from p + bo. Don't trust Claude's e_total or
    duration because the model repeatedly hallucinates the same numbers
    (e.g. 23.4 / 80 min) across different matches. Score breakdowns are also
    overridden — they must be mathematically consistent with p.
    """
    dist = dict(dist or {})
    # Score distribution by best-of (ALWAYS overwrite)
    if bo == 5:
        s = _solve_set_prob(p, bo=5)
        dist['3-0'] = round(s ** 3, 3)
        dist['3-1'] = round(3 * s ** 3 * (1 - s), 3)
        dist['3-2'] = round(6 * s ** 3 * (1 - s) ** 2, 3)
        dist['0-3'] = round((1 - s) ** 3, 3)
        dist['1-3'] = round(3 * (1 - s) ** 3 * s, 3)
        dist['2-3'] = round(6 * (1 - s) ** 3 * s ** 2, 3)
    else:
        s = _solve_set_prob(p, bo=3)
        dist['2-0'] = round(s * s, 3)
        dist['2-1'] = round(2 * s * s * (1 - s), 3)
        dist['0-2'] = round((1 - s) ** 2, 3)
        dist['1-2'] = round(2 * (1 - s) ** 2 * s, 3)

    # Per-set total games depends on closeness
    closeness = 1 - abs(p - 0.5) * 2  # 1 at 50/50, 0 at lopsided
    avg_games_per_set = 9.4 + 1.0 * closeness  # 9.4..10.4 games / set
    played = sum_played_sets(bo, dist)
    dist['e_total'] = round(avg_games_per_set * played, 1)
    # Individual totals: split with mild skew toward favorite
    skew = 0.5 + 0.05 * (p - 0.5) * 4  # 0.5..0.6
    dist['e_fav'] = round(dist['e_total'] * skew, 1)
    dist['e_dog'] = round(dist['e_total'] - dist['e_fav'], 1)
    # Handicap rounded to .5
    dist['handicap'] = max(0.5, round((dist['e_fav'] - dist['e_dog']) * 2) / 2)
    # Tiebreak probability: 0.30..0.75 by closeness
    dist['p_tiebreak'] = round(0.30 + 0.45 * closeness, 2)
    # Duration: 26..32 min / set
    minutes_per_set = 26 + 6 * closeness
    dist['e_duration'] = int(minutes_per_set * played)
    return dist


def sum_played_sets(bo: int, dist: dict) -> float:
    """Expected number of sets played, used to derive E(total games) and duration."""
    if bo == 5:
        # 3 sets if 3-0 or 0-3; 4 sets if 3-1 or 1-3; 5 sets if 3-2 or 2-3
        return (
            3 * (dist.get('3-0', 0) + dist.get('0-3', 0))
            + 4 * (dist.get('3-1', 0) + dist.get('1-3', 0))
            + 5 * (dist.get('3-2', 0) + dist.get('2-3', 0))
        )
    return (
        2 * (dist.get('2-0', 0) + dist.get('0-2', 0))
        + 3 * (dist.get('2-1', 0) + dist.get('1-2', 0))
    )


def _solve_set_prob(p_match: float, bo: int) -> float:
    """Numerically invert match-prob → per-set prob. Bisection on monotone func."""
    lo, hi = 0.05, 0.95
    target = max(0.02, min(0.98, p_match))
    for _ in range(36):
        mid = (lo + hi) / 2
        if bo == 5:
            # P(match) = s^3 (1 + 3(1-s) + 6(1-s)^2)
            pm = mid ** 3 * (1 + 3 * (1 - mid) + 6 * (1 - mid) ** 2)
        else:
            # P(match) = s^2 + 2 s^2 (1-s) = s^2 (3 - 2s)
            pm = mid * mid * (3 - 2 * mid)
        if pm < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def generate_pdf(data: dict) -> str:
    """
    Generate a 2-page PDF report from analysis data.
    Returns the file path of the generated PDF.
    """
    os.makedirs(PDF_DIR, exist_ok=True)

    p1 = data.get("player1", {})
    p2 = data.get("player2", {})
    p = data.get("probability", 0.5)
    bo = data.get("bo", 5)
    dist = data.get("distribution", {})
    fav_idx = data.get("favorite", 1)
    fav = p1 if fav_idx == 1 else p2
    dog = p2 if fav_idx == 1 else p1
    mw = CW - 6 * mm

    fn_en = fav.get("name_en", "Player1").replace(" ", "_").replace(".", "")
    dn_en = dog.get("name_en", "Player2").replace(" ", "_").replace(".", "")
    fname = f"{fn_en}_vs_{dn_en}.pdf"
    fpath = os.path.join(PDF_DIR, fname)

    c = canvas.Canvas(fpath, pagesize=A4)

    # ═══════════ PAGE 1 ═══════════
    y = H - 15 * mm

    # Header
    h = 16 * mm
    c.setFillColor(NAVY)
    c.rect(LM, y - h, CW, h, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont(DJSB, 10)
    tour = data.get("tour", "ATP")
    rnd = data.get("round", "")
    c.drawString(TLM, y - 6 * mm, f"{data.get('tournament', 'Tennis')} | {rnd} | {tour}")
    c.setFont(DJS, 7.5)
    c.drawString(TLM, y - 12 * mm,
                 f"{data.get('court', '')} | {data.get('date', '')} | "
                 f"{data.get('surface', 'Грунт')} | Bo{bo}")
    y -= h + 3 * mm

    # VS block
    mid = W / 2
    seed1 = f" [{p1.get('seed', '')}]" if p1.get("seed") else ""
    seed2 = f" [{p2.get('seed', '')}]" if p2.get("seed") else ""

    c.setFillColor(NAVY)
    c.setFont(DJSB, 13)
    c.drawRightString(mid - 8 * mm, y, f"{p1['name']}{seed1}")
    c.setFillColor(GOLD)
    c.setFont(DJSB, 14)
    c.drawCentredString(mid, y, "VS")
    c.setFillColor(NAVY)
    c.setFont(DJSB, 13)
    c.drawString(mid + 8 * mm, y, f"{p2['name']}{seed2}")
    y -= 5 * mm

    c.setFont(DJS, 8)
    c.setFillColor(GRAY)
    c.drawRightString(mid - 8 * mm, y,
                      f"{p1.get('nationality', '')} | ATP {p1.get('rank', '')}")
    c.drawString(mid + 8 * mm, y,
                 f"{p2.get('nationality', '')} | ATP {p2.get('rank', '')}")
    y -= 7 * mm

    # Profiles
    for player, label in [(p1, f"ПРОФИЛЬ: {p1.get('name', '?').upper() if isinstance(p1.get('name'), str) else '?'}"),
                           (p2, f"ПРОФИЛЬ: {p2.get('name', '?').upper() if isinstance(p2.get('name'), str) else '?'}")]:
        y = _sbar(c, label, y, gap=2 * mm)
        c.setFont(DJS, 6)
        c.setFillColor(BLACK)
        profile = player.get("profile", [])
        if isinstance(profile, str):
            profile = [profile]
        elif not isinstance(profile, list):
            profile = [str(profile)]
        for line in profile[:6]:
            y -= 3 * mm
            c.drawString(TLM, y, _fit(c, line, DJS, 6, CW - 6 * mm))
        y -= 1 * mm

    # H2H — multi-line wrap (fixes raw JSON dump bug)
    h2h_text = _to_text(data.get("h2h", "Данные не найдены"))
    y = _sbar(c, "H2H", y, gap=2 * mm)
    c.setFont(DJS, 6)
    c.setFillColor(BLACK)
    for ln in _wrap(c, h2h_text, DJS, 6, CW - 6 * mm)[:3]:
        y -= 3 * mm
        c.drawString(TLM, y, ln)
    y -= 1 * mm

    # Factors
    factors = data.get("factors", [])
    y = _sbar(c, f"ФАКТОРНАЯ КОРРЕКТИРОВКА ({len(factors)} ФАКТОРОВ)", y, gap=2 * mm)
    rh = 3.6 * mm
    c.setFillColor(LBLUE)
    c.rect(LM, y - rh, CW, rh, fill=1, stroke=0)
    c.setFillColor(NAVY)
    c.setFont(DJSB, 6)
    c.drawString(TLM, y - rh + 0.8 * mm, "№")
    c.drawString(LM + 10 * mm, y - rh + 0.8 * mm, "Фактор")
    c.drawString(LM + 60 * mm, y - rh + 0.8 * mm, "Сдвиг")
    c.drawString(LM + 85 * mm, y - rh + 0.8 * mm, "Обоснование")
    y -= rh

    for i, f in enumerate(factors[:8]):
        bg = LGRAY if i % 2 == 0 else WHITE
        c.setFillColor(bg)
        c.rect(LM, y - rh, CW, rh, fill=1, stroke=0)
        c.setFillColor(BLACK)
        c.setFont(DJS, 5.8)
        c.drawString(TLM, y - rh + 0.8 * mm, str(f.get("num", "")))
        c.drawString(LM + 10 * mm, y - rh + 0.8 * mm,
                     _fit(c, f.get("name", ""), DJS, 5.8, 48 * mm))
        shift_text = str(f.get("shift", ""))
        c.setFillColor(BLUE if isinstance(fav.get("name"), str) and fav["name"].split()[-1] in shift_text else ORANGE)
        c.setFont(DJSB, 5.8)
        c.drawString(LM + 60 * mm, y - rh + 0.8 * mm,
                     _fit(c, shift_text, DJSB, 5.8, 23 * mm))
        c.setFillColor(BLACK)
        c.setFont(DJS, 5.8)
        c.drawString(LM + 85 * mm, y - rh + 0.8 * mm,
                     _fit(c, f.get("reason", ""), DJS, 5.8, CW - (85 * mm - LM) - 3 * mm))
        y -= rh

    # Probability bar
    y -= 3 * mm
    y = _sbar(c, "ИТОГОВАЯ ВЕРОЯТНОСТЬ", y, gap=1 * mm)
    bar_h = 6 * mm
    bar_x = LM + 5 * mm
    bar_w = CW - 10 * mm
    c.setFillColor(GREEN)
    c.rect(bar_x, y - bar_h, bar_w * p, bar_h, fill=1, stroke=0)
    c.setFillColor(RED)
    c.rect(bar_x + bar_w * p, y - bar_h, bar_w * (1 - p), bar_h, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont(DJSB, 8)
    c.drawString(bar_x + 2 * mm, y - bar_h + 1.5 * mm,
                 f"{fav['name']} {round(p * 100)}%")
    c.drawRightString(bar_x + bar_w - 2 * mm, y - bar_h + 1.5 * mm,
                      f"{round((1 - p) * 100)}% {dog['name']}")

    _footer(c, "tennis")
    c.showPage()

    # ═══════════ PAGE 2 ═══════════
    y = H - 15 * mm
    h = 10 * mm
    c.setFillColor(NAVY)
    c.rect(LM, y - h, CW, h, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont(DJSB, 10)
    c.drawString(TLM, y - h + 3 * mm,
                 f"РАСШИРЕННЫЙ АНАЛИЗ | {fav.get('name_en', '')} vs {dog.get('name_en', '')}")
    y -= h + 3 * mm

    # Set distribution table — auto-fill with sane model defaults if Claude omitted
    dist = _ensure_tennis_distribution(dist, p, bo)
    y = _sbar(c, "РАСПРЕДЕЛЕНИЕ ПО СЕТАМ + ВЕРОЯТНОСТИ", y, gap=1 * mm)
    rh2 = 3.3 * mm

    hc = dist.get("handicap", 0)
    et = dist.get("e_total", 0)
    ptb = dist.get("p_tiebreak", 0)
    dur = dist.get("e_duration", 0)

    if bo == 5:
        prob_rows = [
            ("Победитель", f"{fav['name']} {round(p * 100)}%", f"{dog['name']} {round((1 - p) * 100)}%"),
            ("Счёт 3-0 / 0-3", f"{round(dist.get('3-0', 0) * 100)}%", f"{round(dist.get('0-3', 0) * 100)}%"),
            ("Счёт 3-1 / 1-3", f"{round(dist.get('3-1', 0) * 100)}%", f"{round(dist.get('1-3', 0) * 100)}%"),
            ("Счёт 3-2 / 2-3", f"{round(dist.get('3-2', 0) * 100)}%", f"{round(dist.get('2-3', 0) * 100)}%"),
        ]
    else:
        prob_rows = [
            ("Победитель", f"{fav['name']} {round(p * 100)}%", f"{dog['name']} {round((1 - p) * 100)}%"),
            ("Счёт 2-0 / 0-2", f"{round(dist.get('2-0', 0) * 100)}%", f"{round(dist.get('0-2', 0) * 100)}%"),
            ("Счёт 2-1 / 1-2", f"{round(dist.get('2-1', 0) * 100)}%", f"{round(dist.get('1-2', 0) * 100)}%"),
        ]

    prob_rows += [
        ("E(total) геймов", f"{et}", ""),
        ("Инд. тотал фав./аутс.", f"{dist.get('e_fav', '')}", f"{dist.get('e_dog', '')}"),
        ("Фора фаворита", f"-{hc}", ""),
        ("Тай-брейк", f"{round(ptb * 100)}%", ""),
        ("Ожид. длительность", f"{dur} мин", ""),
    ]

    for i, (label, v1, v2) in enumerate(prob_rows):
        bg = LBLUE if i % 2 == 0 else WHITE
        c.setFillColor(bg)
        c.rect(LM, y - rh2, CW, rh2, fill=1, stroke=0)
        c.setFillColor(BLACK)
        c.setFont(DJS, 6.5)
        c.drawString(TLM, y - rh2 + 0.8 * mm, label)
        c.setFont(DJSB, 6.5)
        c.drawString(LM + 65 * mm, y - rh2 + 0.8 * mm, v1)
        c.setFont(DJS, 6.5)
        if v2:
            c.drawString(LM + 105 * mm, y - rh2 + 0.8 * mm, v2)
        y -= rh2
    y -= 2 * mm

    # Style analysis
    style = data.get("style_analysis", "")
    if style and isinstance(style, str):
        y = _sbar(c, "СТИЛИСТИЧЕСКИЙ РАЗБОР", y, gap=2 * mm)
        c.setFont(DJS, 6)
        c.setFillColor(BLACK)
        for line in _wrap(c, str(style), DJS, 6, mw):
            y -= 2.8 * mm
            c.drawString(TLM, y, line)
        y -= 2 * mm

    # Conditions
    conditions = data.get("conditions", "")
    if conditions and isinstance(conditions, str):
        y = _sbar(c, "УСЛОВИЯ И ФИЗИЧЕСКИЙ ФАКТОР", y, gap=2 * mm)
        c.setFont(DJS, 6)
        c.setFillColor(BLACK)
        for line in _wrap(c, conditions, DJS, 6, mw):
            y -= 2.8 * mm
            c.drawString(TLM, y, line)

    _footer(c, "tennis")
    c.showPage()

    # ═══════════ PAGE 3 — Scenarios + Verdict ═══════════
    y = H - 15 * mm
    h = 10 * mm
    c.setFillColor(NAVY)
    c.rect(LM, y - h, CW, h, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont(DJSB, 10)
    c.drawString(TLM, y - h + 3 * mm,
                 f"ФИНАЛЬНЫЙ АНАЛИЗ | {fav.get('name_en', '')} vs {dog.get('name_en', '')}")
    y -= h + 3 * mm

    # Scenarios
    scenarios = data.get("scenarios", [])
    if scenarios:
        y = _sbar(c, "КЛЮЧЕВЫЕ СЦЕНАРИИ МАТЧА", y, gap=1 * mm)
        for sc in scenarios[:4]:
            c.setFont(DJSB, 6)
            c.setFillColor(BLUE)
            y -= 3.5 * mm
            c.drawString(TLM, y, _fit(c, sc.get("title", ""), DJSB, 6, CW - 6 * mm))
            c.setFont(DJS, 5.5)
            c.setFillColor(BLACK)
            for wl in _wrap(c, str(sc.get("text", "")), DJS, 5.5, mw):
                y -= 2.5 * mm
                c.drawString(TLM, y, wl)
            y -= 2 * mm
        y -= 2 * mm

    # Final verdict
    verdict = data.get("verdict", "")
    if verdict:
        vh = 6 * mm
        c.setFillColor(GREEN)
        c.rect(LM, y - vh, CW, vh, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont(DJSB, 8)
        c.drawString(TLM, y - vh + 1.8 * mm,
                     f"ФИНАЛЬНЫЙ ВЕРДИКТ: {fav['name']} {round(p * 100)}% | "
                     f"E(total)={et} | Фора: -{hc}")
        y -= vh + 2 * mm

        c.setFont(DJS, 6)
        c.setFillColor(BLACK)
        for line in _wrap(c, verdict, DJS, 6, mw):
            y -= 3 * mm
            c.drawString(TLM, y, line)
        y -= 4 * mm

    # Confidence badge
    confidence = data.get("confidence", "")
    if confidence:
        ci_h = 4.5 * mm
        c.setFillColor(GOLD)
        c.rect(LM, y - ci_h, CW, ci_h, fill=1, stroke=0)
        c.setFillColor(NAVY)
        c.setFont(DJSB, 6.5)
        c.drawString(TLM, y - ci_h + 1 * mm,
                     f"УВЕРЕННОСТЬ: {confidence} | Методология v5")

    _footer(c, "tennis")
    c.showPage()
    c.save()

    return fpath


def generate_esports_pdf(data: dict, sport: str = "cs2") -> str:
    """
    Generate a 2-page PDF report for CS2 / Dota 2 analysis.
    `data` has team1/team2 shape (name, short, hltv_rank/liquipedia_rank, etc).
    Returns the file path of the generated PDF.
    """
    os.makedirs(PDF_DIR, exist_ok=True)

    sport = (sport or "cs2").lower()
    is_dota = sport.startswith("dota")
    sport_label = "Dota 2" if is_dota else "CS2"
    # DejaVu does not render colour emoji — keep ASCII tags instead of the boxes.
    sport_emoji = "[D2]" if is_dota else "[CS]"
    accent = RED if is_dota else HexColor('#E67E22')
    rank_key = "liquipedia_rank" if is_dota else "hltv_rank"
    rank_label = "Liquipedia" if is_dota else "HLTV"
    footer_tag = "dota2" if is_dota else "cs2"

    t1 = data.get("team1", {}) or {}
    t2 = data.get("team2", {}) or {}
    if not isinstance(t1, dict): t1 = {"name": str(t1)}
    if not isinstance(t2, dict): t2 = {"name": str(t2)}

    p = float(data.get("probability", 0.5) or 0.5)
    fmt = str(data.get("format", "Bo3"))
    fav_idx = data.get("favorite", 1)
    fav = t1 if fav_idx == 1 else t2
    dog = t2 if fav_idx == 1 else t1
    dist = data.get("distribution", {}) or {}
    mw = CW - 6 * mm

    def _safe_name(d, default):
        n = d.get("name") or d.get("short") or default
        return str(n)

    fav_name = _safe_name(fav, "Team1")
    dog_name = _safe_name(dog, "Team2")
    t1_name = _safe_name(t1, "Team1")
    t2_name = _safe_name(t2, "Team2")
    fav_short = str(fav.get("short") or fav_name)
    dog_short = str(dog.get("short") or dog_name)

    fn_en = fav_name.replace(" ", "_").replace(".", "")
    dn_en = dog_name.replace(" ", "_").replace(".", "")
    prefix = "Dota2" if is_dota else "CS2"
    fname = f"{prefix}_{fn_en}_vs_{dn_en}.pdf"
    fpath = os.path.join(PDF_DIR, fname)

    c = canvas.Canvas(fpath, pagesize=A4)

    # ═══════════ PAGE 1 ═══════════
    y = H - 15 * mm

    # Header
    h = 16 * mm
    c.setFillColor(NAVY)
    c.rect(LM, y - h, CW, h, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont(DJSB, 10)
    tour = str(data.get("tournament", sport_label))
    stage = str(data.get("stage", "") or data.get("round", ""))
    c.drawString(TLM, y - 6 * mm, f"{sport_emoji} {sport_label} | {tour} | {stage}")
    c.setFont(DJS, 7.5)
    c.drawString(TLM, y - 12 * mm,
                 f"{data.get('date', '')} | Format: {fmt}")
    y -= h + 3 * mm

    # VS block
    mid = W / 2
    c.setFillColor(NAVY)
    c.setFont(DJSB, 13)
    c.drawRightString(mid - 8 * mm, y, f"{t1_name} [{t1.get('short', '')}]")
    c.setFillColor(GOLD)
    c.setFont(DJSB, 14)
    c.drawCentredString(mid, y, "VS")
    c.setFillColor(NAVY)
    c.setFont(DJSB, 13)
    c.drawString(mid + 8 * mm, y, f"{t2_name} [{t2.get('short', '')}]")
    y -= 5 * mm

    c.setFont(DJS, 8)
    c.setFillColor(GRAY)
    c.drawRightString(mid - 8 * mm, y, f"{rank_label} #{t1.get(rank_key, '?')}")
    c.drawString(mid + 8 * mm, y, f"{rank_label} #{t2.get(rank_key, '?')}")
    y -= 7 * mm

    # Profiles (if present)
    for team, name in [(t1, t1_name), (t2, t2_name)]:
        profile = team.get("profile", [])
        if isinstance(profile, str):
            profile = [profile]
        elif not isinstance(profile, list):
            profile = [str(profile)] if profile else []
        if not profile:
            continue
        y = _sbar(c, f"ПРОФИЛЬ: {str(name).upper()}", y, gap=2 * mm)
        c.setFont(DJS, 6)
        c.setFillColor(BLACK)
        for line in profile[:5]:
            y -= 3 * mm
            c.drawString(TLM, y, str(line)[:125])
        y -= 1 * mm

    # Player status
    ps = data.get("player_status", {}) or {}
    if isinstance(ps, dict) and (ps.get("team1") or ps.get("team2")):
        y = _sbar(c, "СОСТОЯНИЕ ИГРОКОВ / РОСТЕР", y, gap=2 * mm)
        c.setFont(DJS, 6)
        c.setFillColor(BLACK)
        for key, label in [("team1", t1_name), ("team2", t2_name)]:
            v = ps.get(key)
            if not v:
                continue
            y -= 3 * mm
            c.drawString(TLM, y,
                         _fit(c, f"{_to_text(label)}: {_to_text(v)}", DJS, 6, CW - 6 * mm))
        y -= 1 * mm

    # H2H — wrap into 3 lines max to avoid the truncation bug
    h2h_text = _to_text(data.get("h2h", ""))
    if h2h_text:
        y = _sbar(c, "H2H", y, gap=2 * mm)
        c.setFont(DJS, 6)
        c.setFillColor(BLACK)
        for ln in _wrap(c, h2h_text, DJS, 6, CW - 6 * mm)[:3]:
            y -= 3 * mm
            c.drawString(TLM, y, ln)
        y -= 1 * mm

    # Factors
    factors = data.get("factors", []) or []
    if isinstance(factors, list) and factors:
        y = _sbar(c, f"ФАКТОРНАЯ КОРРЕКТИРОВКА ({len(factors)} ФАКТОРОВ)", y, gap=2 * mm)
        rh = 3.6 * mm
        c.setFillColor(LBLUE)
        c.rect(LM, y - rh, CW, rh, fill=1, stroke=0)
        c.setFillColor(NAVY)
        c.setFont(DJSB, 6)
        c.drawString(TLM, y - rh + 0.8 * mm, "№")
        c.drawString(LM + 10 * mm, y - rh + 0.8 * mm, "Фактор")
        c.drawString(LM + 60 * mm, y - rh + 0.8 * mm, "Сдвиг")
        c.drawString(LM + 85 * mm, y - rh + 0.8 * mm, "Обоснование")
        y -= rh

        for i, f in enumerate(factors[:8]):
            if not isinstance(f, dict):
                f = {"name": str(f)}
            bg = LGRAY if i % 2 == 0 else WHITE
            c.setFillColor(bg)
            c.rect(LM, y - rh, CW, rh, fill=1, stroke=0)
            c.setFillColor(BLACK)
            c.setFont(DJS, 5.8)
            c.drawString(TLM, y - rh + 0.8 * mm, str(f.get("num", i + 1)))
            c.drawString(LM + 10 * mm, y - rh + 0.8 * mm,
                         _fit(c, f.get("name", ""), DJS, 5.8, 48 * mm))
            shift_text = str(f.get("shift", ""))
            try:
                c.setFillColor(BLUE if fav_short.split()[-1].lower() in shift_text.lower() else ORANGE)
            except Exception:
                c.setFillColor(BLUE)
            c.setFont(DJSB, 5.8)
            c.drawString(LM + 60 * mm, y - rh + 0.8 * mm,
                         _fit(c, shift_text, DJSB, 5.8, 23 * mm))
            c.setFillColor(BLACK)
            c.setFont(DJS, 5.8)
            c.drawString(LM + 85 * mm, y - rh + 0.8 * mm,
                         _fit(c, f.get("reason", ""), DJS, 5.8, CW - (85 * mm - LM) - 3 * mm))
            y -= rh

    # Probability bar
    y -= 3 * mm
    y = _sbar(c, "ИТОГОВАЯ ВЕРОЯТНОСТЬ", y, gap=1 * mm)
    bar_h = 6 * mm
    bar_x = LM + 5 * mm
    bar_w = CW - 10 * mm
    c.setFillColor(GREEN)
    c.rect(bar_x, y - bar_h, bar_w * p, bar_h, fill=1, stroke=0)
    c.setFillColor(RED)
    c.rect(bar_x + bar_w * p, y - bar_h, bar_w * (1 - p), bar_h, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont(DJSB, 8)
    c.drawString(bar_x + 2 * mm, y - bar_h + 1.5 * mm,
                 f"{fav_short} {round(p * 100)}%")
    c.drawRightString(bar_x + bar_w - 2 * mm, y - bar_h + 1.5 * mm,
                      f"{round((1 - p) * 100)}% {dog_short}")

    _footer(c, footer_tag)
    c.showPage()

    # ═══════════ PAGE 2 ═══════════
    y = H - 15 * mm
    h = 10 * mm
    c.setFillColor(NAVY)
    c.rect(LM, y - h, CW, h, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont(DJSB, 10)
    c.drawString(TLM, y - h + 3 * mm,
                 f"{sport_emoji} РАСШИРЕННЫЙ АНАЛИЗ | {fav_name} vs {dog_name}")
    y -= h + 3 * mm

    # Score distribution by format
    y = _sbar(c, "РАСПРЕДЕЛЕНИЕ ПО КАРТАМ + ВЕРОЯТНОСТИ", y, gap=1 * mm)
    rh2 = 3.3 * mm

    if fmt.lower() == "bo5":
        prob_rows = [
            ("Победитель", f"{fav_short} {round(p * 100)}%", f"{dog_short} {round((1 - p) * 100)}%"),
            ("Счёт 3-0 / 0-3", f"{round(dist.get('3-0', 0) * 100)}%", f"{round(dist.get('0-3', 0) * 100)}%"),
            ("Счёт 3-1 / 1-3", f"{round(dist.get('3-1', 0) * 100)}%", f"{round(dist.get('1-3', 0) * 100)}%"),
            ("Счёт 3-2 / 2-3", f"{round(dist.get('3-2', 0) * 100)}%", f"{round(dist.get('2-3', 0) * 100)}%"),
        ]
    elif fmt.lower() == "bo1":
        prob_rows = [
            ("Победитель", f"{fav_short} {round(p * 100)}%", f"{dog_short} {round((1 - p) * 100)}%"),
        ]
    else:  # Bo3 default
        prob_rows = [
            ("Победитель", f"{fav_short} {round(p * 100)}%", f"{dog_short} {round((1 - p) * 100)}%"),
            ("Счёт 2-0 / 0-2", f"{round(dist.get('2-0', 0) * 100)}%", f"{round(dist.get('0-2', 0) * 100)}%"),
            ("Счёт 2-1 / 1-2", f"{round(dist.get('2-1', 0) * 100)}%", f"{round(dist.get('1-2', 0) * 100)}%"),
        ]

    for i, (label, v1, v2) in enumerate(prob_rows):
        bg = LBLUE if i % 2 == 0 else WHITE
        c.setFillColor(bg)
        c.rect(LM, y - rh2, CW, rh2, fill=1, stroke=0)
        c.setFillColor(BLACK)
        c.setFont(DJS, 6.5)
        c.drawString(TLM, y - rh2 + 0.8 * mm, label)
        c.setFont(DJSB, 6.5)
        c.drawString(LM + 65 * mm, y - rh2 + 0.8 * mm, v1)
        c.setFont(DJS, 6.5)
        if v2:
            c.drawString(LM + 105 * mm, y - rh2 + 0.8 * mm, v2)
        y -= rh2
    y -= 2 * mm

    # Map veto / draft — only draw section if at least one sub-field has content
    veto = data.get("map_veto", {}) or {}
    veto_has_content = (
        isinstance(veto, dict)
        and any(veto.get(k) for k in ("bans", "picks", "expected_maps"))
    )
    if veto_has_content:
        title = "ОЖИДАЕМЫЕ ГЕРОИ / ДРАФТ" if is_dota else "ВЕТО КАРТ / ОЖИДАЕМЫЙ МАП-ПУЛ"
        y = _sbar(c, title, y, gap=2 * mm)
        c.setFont(DJS, 6)
        c.setFillColor(BLACK)
        for key in ("bans", "picks", "expected_maps"):
            v = veto.get(key)
            if not v:
                continue
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v)
            y -= 3 * mm
            c.drawString(TLM, y, _fit(c, f"{key}: {_to_text(v)}", DJS, 6, CW - 6 * mm))
        y -= 2 * mm

    # Per-map win-rate bar chart (CS2 only)
    if not is_dota:
        wr1 = t1.get("map_winrates") or {}
        wr2 = t2.get("map_winrates") or {}
        if isinstance(wr1, dict) and isinstance(wr2, dict) and (wr1 or wr2):
            y = _sbar(c, "WIN-RATE ПО КАРТАМ (последние 3 мес.)", y, gap=2 * mm)
            # Map order: union, but keep canonical active duty first
            canonical = ["Mirage", "Inferno", "Nuke", "Anubis", "Ancient",
                         "Dust2", "Dust 2", "Overpass", "Vertigo"]
            seen = []
            for m in canonical:
                if m in wr1 or m in wr2:
                    seen.append(m)
            for m in list(wr1.keys()) + list(wr2.keys()):
                if m not in seen:
                    seen.append(m)

            row_h = 4.5 * mm
            label_x = TLM
            bar1_x = LM + 38 * mm
            bar_w = 55 * mm
            bar2_x = bar1_x + bar_w + 6 * mm  # leaves a gap for the team name
            # Header row
            c.setFont(DJSB, 5.8)
            c.setFillColor(NAVY)
            c.drawString(label_x, y - 2.5 * mm, "Карта")
            c.drawString(bar1_x, y - 2.5 * mm, _fit(c, t1_name, DJSB, 5.8, bar_w))
            c.drawString(bar2_x, y - 2.5 * mm, _fit(c, t2_name, DJSB, 5.8, bar_w))
            y -= 4 * mm
            for m in seen[:7]:
                w1 = float(wr1.get(m, 0) or 0)
                w2 = float(wr2.get(m, 0) or 0)
                # Normalize: 0..1 if Claude gave fraction, 0..100 if percent
                if w1 > 1: w1 /= 100.0
                if w2 > 1: w2 /= 100.0
                # Row label
                c.setFillColor(BLACK)
                c.setFont(DJS, 6)
                c.drawString(label_x, y - row_h + 1.2 * mm, _fit(c, m, DJS, 6, 35 * mm))
                # Bars
                for (bx, w, color) in [
                    (bar1_x, w1, BLUE),
                    (bar2_x, w2, accent),
                ]:
                    # Track
                    c.setFillColor(LGRAY)
                    c.rect(bx, y - row_h + 1 * mm, bar_w, 2.5 * mm, fill=1, stroke=0)
                    # Filled portion
                    if w > 0:
                        c.setFillColor(color)
                        c.rect(bx, y - row_h + 1 * mm, bar_w * w, 2.5 * mm, fill=1, stroke=0)
                    # Numeric label
                    c.setFillColor(BLACK)
                    c.setFont(DJSB, 5.5)
                    c.drawString(bx + bar_w + 1 * mm, y - row_h + 1.4 * mm,
                                 f"{round(w * 100)}%")
                y -= row_h
            y -= 2 * mm

    # Style / conditions
    for key, title in [("style_analysis", "СТИЛИСТИЧЕСКИЙ РАЗБОР"),
                       ("conditions", "УСЛОВИЯ И КОНТЕКСТ")]:
        v = data.get(key, "")
        if v and isinstance(v, str):
            y = _sbar(c, title, y, gap=2 * mm)
            c.setFont(DJS, 6)
            c.setFillColor(BLACK)
            for line in _wrap(c, str(v), DJS, 6, mw):
                y -= 2.8 * mm
                c.drawString(TLM, y, line)
            y -= 2 * mm

    # Scenarios
    scenarios = data.get("scenarios", []) or []
    if isinstance(scenarios, list) and scenarios:
        y = _sbar(c, "КЛЮЧЕВЫЕ СЦЕНАРИИ МАТЧА", y, gap=1 * mm)
        for sc in scenarios[:3]:
            if not isinstance(sc, dict):
                sc = {"text": str(sc)}
            c.setFont(DJSB, 6)
            c.setFillColor(BLUE)
            y -= 3.5 * mm
            c.drawString(TLM, y, _fit(c, sc.get("title", ""), DJSB, 6, CW - 6 * mm))
            c.setFont(DJS, 5.5)
            c.setFillColor(BLACK)
            for wl in _wrap(c, str(sc.get("text", "")), DJS, 5.5, mw):
                y -= 2.5 * mm
                c.drawString(TLM, y, wl)
            y -= 2 * mm

    # Verdict
    verdict = data.get("verdict", "")
    if verdict:
        vh = 6 * mm
        c.setFillColor(GREEN)
        c.rect(LM, y - vh, CW, vh, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont(DJSB, 8)
        c.drawString(TLM, y - vh + 1.8 * mm,
                     f"ФИНАЛЬНЫЙ ВЕРДИКТ: {fav_short} {round(p * 100)}% | {fmt}")
        y -= vh + 2 * mm

        c.setFont(DJS, 6)
        c.setFillColor(BLACK)
        for line in _wrap(c, str(verdict), DJS, 6, mw):
            y -= 3 * mm
            c.drawString(TLM, y, line)
        y -= 4 * mm

    # Confidence badge
    confidence = data.get("confidence", "")
    if confidence:
        ci_h = 4.5 * mm
        c.setFillColor(GOLD)
        c.rect(LM, y - ci_h, CW, ci_h, fill=1, stroke=0)
        c.setFillColor(NAVY)
        c.setFont(DJSB, 6.5)
        c.drawString(TLM, y - ci_h + 1 * mm,
                     f"УВЕРЕННОСТЬ: {confidence} | {sport_label} Methodology v2")

    _footer(c, footer_tag)
    c.showPage()
    c.save()

    return fpath


def generate_today_pdf(text: str, date_str: str, lang: str = "ru") -> str:
    """Generate a clean PDF with today's match schedule."""
    os.makedirs(PDF_DIR, exist_ok=True)
    fpath = os.path.join(PDF_DIR, f"schedule_{date_str.replace(' ', '_')}.pdf")

    c = canvas.Canvas(fpath, pagesize=A4)
    mw = CW - 6 * mm

    # ═══ Header ═══
    y = H - 15 * mm
    h = 18 * mm
    c.setFillColor(NAVY)
    c.rect(LM, y - h, CW, h, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont(DJSB, 13)
    title = f"TODAY'S MATCHES | {date_str}" if lang == "en" else f"РАСПИСАНИЕ МАТЧЕЙ | {date_str}"
    c.drawString(TLM, y - 7 * mm, title)
    c.setFont(DJS, 8)
    c.drawString(TLM, y - 13 * mm, "Tennis | CS2 | Dota 2")
    y -= h + 4 * mm

    # ═══ Body ═══
    lines = text.split('\n')
    row_idx = 0

    for line in lines:
        line = line.strip()
        if not line:
            y -= 2 * mm
            continue

        # Page break check
        if y < 25 * mm:
            _footer(c, "tennis")
            c.showPage()
            y = H - 15 * mm
            row_idx = 0

        # Section headers (emoji or caps)
        is_section = any(line.startswith(e) for e in ['🎾', '🎮', '⚔️', 'TENNIS', 'CS2', 'DOTA', 'ТЕННИС'])
        if is_section or (line.isupper() and len(line) < 40):
            clean = line.replace('🎾', '').replace('🎮', '').replace('⚔️', '').strip()
            # Pick color by sport
            if any(k in line.upper() for k in ['TENNIS', 'ТЕННИС', '🎾']):
                bar_color = GREEN
            elif any(k in line.upper() for k in ['CS2', 'CS', '🎮']):
                bar_color = HexColor('#E67E22')
            elif any(k in line.upper() for k in ['DOTA', '⚔️']):
                bar_color = RED
            else:
                bar_color = BLUE

            y -= 3 * mm
            bar_h = 6 * mm
            c.setFillColor(bar_color)
            c.rect(LM, y - bar_h, CW, bar_h, fill=1, stroke=0)
            c.setFillColor(WHITE)
            c.setFont(DJSB, 8)
            c.drawString(TLM, y - bar_h + 1.8 * mm, clean[:70])
            y -= bar_h + 2 * mm
            row_idx = 0
            continue

        # Match lines (• or - or numbered)
        is_match = line.startswith('•') or line.startswith('-') or (len(line) > 2 and line[0].isdigit() and line[1] in '.)')
        if is_match:
            clean = line.lstrip('•-0123456789.) ').strip()

            # Alternating row background
            rh = 4.5 * mm
            bg = LGRAY if row_idx % 2 == 0 else WHITE
            c.setFillColor(bg)
            c.rect(LM, y - rh, CW, rh, fill=1, stroke=0)

            # Try to split time and match
            c.setFillColor(BLACK)
            if '—' in clean:
                parts = clean.split('—', 1)
                time_str = parts[0].strip()
                match_str = parts[1].strip()
                # Time in bold
                c.setFont(DJSB, 7)
                c.drawString(TLM, y - rh + 1.2 * mm, time_str)
                # Match info
                c.setFont(DJS, 7)
                c.drawString(TLM + 18 * mm, y - rh + 1.2 * mm, match_str[:95])
            else:
                c.setFont(DJS, 7)
                c.drawString(TLM, y - rh + 1.2 * mm, clean[:110])

            y -= rh
            row_idx += 1
            continue

        # Other text (notes, explanations)
        if any(k in line.lower() for k in ['правил', 'rules', 'важно', 'important', 'никаких', 'нет матчей', 'no matches']):
            continue  # Skip instruction artifacts

        # Fallback: regular text
        c.setFont(DJS, 6.5)
        c.setFillColor(GRAY)
        for wl in _wrap(c, line, DJS, 6.5, mw):
            if y < 25 * mm:
                _footer(c, "tennis")
                c.showPage()
                y = H - 15 * mm
            y -= 3 * mm
            c.drawString(TLM, y, wl)
        c.setFillColor(BLACK)

    # ═══ Footer note ═══
    y -= 8 * mm
    if lang == "en":
        note = "For analysis: /analyze (tennis) | /cs2 (CS2) | /dota2 (Dota 2)"
    else:
        note = "Для анализа: /analyze (теннис) | /cs2 (CS2) | /dota2 (Dota 2)"
    c.setFont(DJSB, 7)
    c.setFillColor(BLUE)
    c.drawString(TLM, y, note)

    _footer(c, "tennis")
    c.showPage()
    c.save()

    return fpath
