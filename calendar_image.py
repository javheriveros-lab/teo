"""Genera la imagen del calendario semanal del plan de optimización de TEO."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS_DIR = Path(__file__).parent / "fonts"
FONT_REGULAR = FONTS_DIR / "Roboto-Regular.ttf"
FONT_BOLD = FONTS_DIR / "Roboto-Bold.ttf"

WIDTH = 1000
MARGIN = 30
HEADER_H = 150
ROW_H = 70
COL_DAY_W = 140
COL_TRAIN_W = 330
COL_FOOD_W = WIDTH - 2 * MARGIN - COL_DAY_W - COL_TRAIN_W

COLOR_BG = (255, 255, 255)
COLOR_HEADER_BG = (20, 90, 70)
COLOR_HEADER_TEXT = (255, 255, 255)
COLOR_TEXT = (30, 30, 30)
COLOR_REST_BG = (235, 235, 235)
COLOR_ROW_ALT = (244, 249, 247)
COLOR_GRID = (210, 210, 210)
COLOR_ACCENT = (20, 90, 70)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold else FONT_REGULAR
    return ImageFont.truetype(str(path), size)


# Vista general por categoría del catálogo — mantener en sync manualmente con la sección
# "CATÁLOGO DE PROTOCOLOS/COMBOS" del SYSTEM_PROMPT en main.py si ese catálogo cambia.
CATALOG_CATEGORIES = [
    ("Digestivo", [
        "Combo Detox", "Protocolo Fluidez Digestiva", "Combo Restauración Digestiva",
        "Combo Reset Digestivo", "Combo Diverticulosis", "Combo Salud de Colon",
        "Combo Restauración Mucosa", "Combo Asimilación de Nutrientes", "Combo Cistitis",
        "Combo Colitis", "Combo Ligereza Abdominal", "Combo Drenaje Sistémico",
    ]),
    ("Cardiovascular y circulación", [
        "Combo Blindaje Cardiovascular", "Combo Circulatorio Muscular", "Combo Buena Circulación",
        "Combo Lípidos", "Combo Varices", "Combo Úlcera Varicosa", "Combo Hemorroides",
    ]),
    ("Inmune y respiratorio", [
        "Combo Alergias", "Combo Respiratorio", "Combo Blindaje Respiratorio",
        "Protocolo Blindaje Inmunológico", "Combo Fibrosis Quística",
    ]),
    ("Cognitivo y emocional", [
        "Combo Longevidad Celular", "Combo Blindaje Neuro-Cognitivo", "Protocolo Blindaje Cognitivo",
        "Protocolo Estabilización Neuro-Emocional", "Combo Migraña", "Combo Optimización Cognitiva",
        "Combo Agudeza Visual",
    ]),
    ("Articular y óseo", [
        "Protocolo Reconstrucción Articular", "Combo Densidad Ósea",
    ]),
    ("Metabólico y peso", [
        "Protocolo Control Glucémico", "Protocolo Sobrepeso", "Combo Hipertrofia",
        "Vitality Upgrade System", "Combo Anemia", "Protocolo Desinflamación",
    ]),
    ("Estética y piel", [
        "Protocolo Estética Estructural", "Combo Armonización Dérmica",
    ]),
    ("Vitalidad y próstata", [
        "Combo Vitalidad Hormonal", "Combo Soporte Prostático",
    ]),
    ("Niños", [
        "Combo Desarrollo Escolar", "Combo Crecimiento Infantil",
    ]),
]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list:
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_weekly_calendar(
    protocol_name: str,
    sup_time: str,
    meal_label: str,
    ent_time: str,
    weekly_plan: list,
) -> bytes:
    """weekly_plan: lista de 7 tuplas (day_of_week, food_suggestion, training_focus),
    en orden Lunes(0)..Domingo(6). Devuelve bytes PNG listos para subir a WhatsApp."""
    day_names = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

    height = HEADER_H + ROW_H * 7 + MARGIN
    img = Image.new("RGB", (WIDTH, height), COLOR_BG)
    draw = ImageDraw.Draw(img)

    title_font = _font(30, bold=True)
    subtitle_font = _font(20)
    header_font = _font(20, bold=True)
    body_font = _font(18)

    draw.rectangle([0, 0, WIDTH, HEADER_H], fill=COLOR_HEADER_BG)
    draw.text((MARGIN, 20), "Plan de optimización I'AM · 1 mes (4 semanas)", font=title_font, fill=COLOR_HEADER_TEXT)
    draw.text((MARGIN, 62), f"Protocolo: {protocol_name}", font=subtitle_font, fill=COLOR_HEADER_TEXT)
    draw.text(
        (MARGIN, 92),
        f"Suplemento {sup_time} junto con {meal_label}   ·   Entrenamiento {ent_time}",
        font=subtitle_font,
        fill=COLOR_HEADER_TEXT,
    )

    col_x = [MARGIN, MARGIN + COL_DAY_W, MARGIN + COL_DAY_W + COL_TRAIN_W]
    headers = ["Día", "Entrenamiento", "Sugerencia de comida"]
    header_y = HEADER_H
    draw.rectangle([MARGIN, header_y, WIDTH - MARGIN, header_y + 44], fill=COLOR_ACCENT)
    for x, text in zip(col_x, headers):
        draw.text((x + 10, header_y + 10), text, font=header_font, fill=COLOR_HEADER_TEXT)

    y = header_y + 44
    ordered = sorted(weekly_plan, key=lambda row: row[0])
    for index, (day_index, food, training) in enumerate(ordered):
        is_rest = training.upper() == "DESCANSO"
        row_bg = COLOR_REST_BG if is_rest else (COLOR_ROW_ALT if index % 2 else COLOR_BG)
        draw.rectangle([MARGIN, y, WIDTH - MARGIN, y + ROW_H], fill=row_bg)

        draw.text((col_x[0] + 10, y + 10), day_names[day_index], font=body_font, fill=COLOR_TEXT)

        training_label = "Descanso" if is_rest else training.capitalize()
        for i, line in enumerate(_wrap(draw, training_label, body_font, COL_TRAIN_W - 20)[:2]):
            draw.text((col_x[1] + 10, y + 8 + i * 22), line, font=body_font, fill=COLOR_TEXT)

        if not is_rest:
            for i, line in enumerate(_wrap(draw, food, body_font, COL_FOOD_W - 20)[:2]):
                draw.text((col_x[2] + 10, y + 8 + i * 22), line, font=body_font, fill=COLOR_TEXT)

        y += ROW_H

    for x in [*col_x, WIDTH - MARGIN]:
        draw.line([(x, header_y), (x, height - MARGIN + ROW_H - ROW_H)], fill=COLOR_GRID)
    draw.rectangle([MARGIN, header_y, WIDTH - MARGIN, y], outline=COLOR_GRID)

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def render_catalog_infographic() -> bytes:
    """Infografía fija de referencia con el catálogo de protocolos agrupados por categoría.
    Es estática (no personalizada por cliente): se genera una sola vez y se reutiliza para \
    cualquiera que pida "ver los protocolos" o algo similar."""
    title_font = _font(34, bold=True)
    subtitle_font = _font(18)
    category_font = _font(20, bold=True)
    item_font = _font(17)

    col_width = (WIDTH - 3 * MARGIN) // 2
    line_h = 25
    cat_gap = 14
    cat_header_h = 30

    def _category_height(category):
        _name, items = category
        return cat_header_h + len(items) * line_h + cat_gap

    def _column_height(categories):
        return sum(_category_height(c) for c in categories)

    # Reparte categorías por altura real (voraz), no por cantidad, para que ambas columnas
    # queden visualmente balanceadas sin importar cuántos ítems tenga cada categoría.
    left_categories, right_categories = [], []
    left_h = right_h = 0
    for category in CATALOG_CATEGORIES:
        if left_h <= right_h:
            left_categories.append(category)
            left_h += _category_height(category)
        else:
            right_categories.append(category)
            right_h += _category_height(category)

    content_h = max(left_h, right_h)
    height = HEADER_H + content_h + MARGIN * 2

    img = Image.new("RGB", (WIDTH, height), COLOR_BG)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, WIDTH, HEADER_H], fill=COLOR_HEADER_BG)
    draw.text((MARGIN, 30), "Catálogo de Protocolos I'AM", font=title_font, fill=COLOR_HEADER_TEXT)
    draw.text(
        (MARGIN, 82),
        "Optimización biológica por categoría — cuéntame tu caso y te recomiendo el indicado",
        font=subtitle_font,
        fill=COLOR_HEADER_TEXT,
    )

    def _draw_column(categories, x):
        y = HEADER_H + MARGIN
        for name, items in categories:
            draw.text((x, y), name, font=category_font, fill=COLOR_ACCENT)
            y += cat_header_h
            for item in items:
                draw.text((x + 12, y), f"• {item}", font=item_font, fill=COLOR_TEXT)
                y += line_h
            y += cat_gap

    _draw_column(left_categories, MARGIN)
    _draw_column(right_categories, MARGIN * 2 + col_width)

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()
