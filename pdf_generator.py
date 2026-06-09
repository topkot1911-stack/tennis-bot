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


def _wrap(c, text, font, size, max_w):
    """Word-wrap text into lines."""
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


def _sbar(c, title, y, gap=3 * mm):
    """Section bar."""
    y -= gap
    h = 5.5 * mm
    c.setFillColor(BLUE)
    c.rect(LM, y - h, CW, h, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont(DJSB, 7.5)
    c.drawString(TLM, y - h + 1.5 * mm, title)
    return y - h - 1 * mm


def _footer(c):
    """Page footer."""
    fh = 4.5 * mm
    fy = 10 * mm
    c.setFillColor(NAVY)
    c.rect(LM, fy, CW, fh, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont(DJS, 5.5)
    c.drawString(TLM, fy + 1.2 * mm,
                 "Tennis Analyst Bot | Исследовательский анализ | Не является рекомендацией по ставкам")


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
            c.drawString(TLM, y, str(line)[:125])
        y -= 1 * mm

    # H2H
    y = _sbar(c, "H2H", y, gap=2 * mm)
    c.setFont(DJS, 6)
    c.setFillColor(BLACK)
    y -= 3 * mm
    c.drawString(TLM, y, str(data.get("h2h", "Данные не найдены"))[:125])
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
        c.drawString(LM + 10 * mm, y - rh + 0.8 * mm, str(f.get("name", ""))[:26])
        shift_text = str(f.get("shift", ""))
        c.setFillColor(BLUE if fav["name"].split()[-1] in shift_text else ORANGE)
        c.setFont(DJSB, 5.8)
        c.drawString(LM + 60 * mm, y - rh + 0.8 * mm, shift_text[:22])
        c.setFillColor(BLACK)
        c.setFont(DJS, 5.8)
        c.drawString(LM + 85 * mm, y - rh + 0.8 * mm, str(f.get("reason", ""))[:45])
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

    _footer(c)
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

    # Set distribution table
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

    _footer(c)
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
            c.drawString(TLM, y, str(sc.get("title", ""))[:95])
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
                     f"УВЕРЕННОСТЬ: {confidence} | Методология v3")

    _footer(c)
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
            _footer(c)
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
                _footer(c)
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

    _footer(c)
    c.showPage()
    c.save()

    return fpath
