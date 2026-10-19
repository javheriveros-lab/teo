"""Genera la imagen del calendario semanal del plan de optimización de TEO."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS_DIR = Path(__file__).parent / "fonts"
FONT_REGULAR = FONTS_DIR / "Roboto-Regular.ttf"
FONT_BOLD = FONTS_DIR / "Roboto-Bold.ttf"

WIDTH = 1000
MARGIN = 36
HEADER_H = 150
ROW_H = 80
COL_DAY_W = 95
COL_SUP_W = 230
COL_TRAIN_W = 260
COL_FOOD_W = WIDTH - 2 * MARGIN - COL_DAY_W - COL_SUP_W - COL_TRAIN_W

# Paleta "zen-futurista": carbón + dorado mate sobre fondo cálido, sin bloques de color —
# separadores finos en vez de celdas rellenas, para una lectura más limpia y elegante.
COLOR_BG = (250, 249, 246)
COLOR_HEADER_BG = (24, 24, 23)
COLOR_HEADER_TEXT = (247, 245, 240)
COLOR_TEXT = (38, 37, 34)
COLOR_TEXT_MUTED = (154, 150, 140)
COLOR_HAIRLINE = (223, 219, 210)
COLOR_ACCENT = (176, 138, 60)


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

    height = HEADER_H + 66 + ROW_H * 7 + MARGIN
    img = Image.new("RGB", (WIDTH, height), COLOR_BG)
    draw = ImageDraw.Draw(img)

    title_font = _font(28, bold=False)
    subtitle_font = _font(17)
    header_font = _font(14, bold=True)
    label_font = _font(17, bold=True)
    body_font = _font(17)
    muted_font = _font(16)

    # Encabezado: carbón sobrio, tipografía ligera, sin franjas de color duras.
    draw.rectangle([0, 0, WIDTH, HEADER_H], fill=COLOR_HEADER_BG)
    draw.text((MARGIN, 32), "Plan de Optimización I'AM", font=title_font, fill=COLOR_HEADER_TEXT)
    draw.text((MARGIN, 72), "1 mes · 4 semanas", font=subtitle_font, fill=COLOR_ACCENT)
    draw.text(
        (MARGIN, 104),
        f"{protocol_name}   ·   Suplemento {sup_time} con {meal_label}   ·   Entrenamiento {ent_time}",
        font=subtitle_font,
        fill=COLOR_HEADER_TEXT,
    )
    draw.line([(MARGIN, HEADER_H - 1), (WIDTH - MARGIN, HEADER_H - 1)], fill=COLOR_ACCENT, width=1)

    col_x = [
        MARGIN,
        MARGIN + COL_DAY_W,
        MARGIN + COL_DAY_W + COL_SUP_W,
        MARGIN + COL_DAY_W + COL_SUP_W + COL_TRAIN_W,
    ]
    headers = ["DÍA", "SUPLEMENTO", "ENTRENAMIENTO", "COMIDA SUGERIDA"]
    header_y = HEADER_H + 26
    for x, text in zip(col_x, headers):
        draw.text((x, header_y), text, font=header_font, fill=COLOR_ACCENT)
    rule_y = header_y + 26
    draw.line([(MARGIN, rule_y), (WIDTH - MARGIN, rule_y)], fill=COLOR_HAIRLINE, width=1)

    y = rule_y + 14
    ordered = sorted(weekly_plan, key=lambda row: row[0])
    for day_index, food, training in ordered:
        is_rest = training.upper() == "DESCANSO"
        day_color = COLOR_TEXT_MUTED if is_rest else COLOR_TEXT

        draw.text((col_x[0], y + 12), day_names[day_index], font=label_font, fill=day_color)

        # Suplementación: mismo horario y protocolo todos los días (no varía como el entrenamiento,
        # continúa incluso en día de descanso), para que los tres pilares queden juntos en cada fila.
        sup_label = f"{protocol_name} — {sup_time} con {meal_label}"
        for i, line in enumerate(_wrap(draw, sup_label, body_font, COL_SUP_W - 16)[:3]):
            draw.text((col_x[1], y + 10 + i * 22), line, font=body_font, fill=COLOR_TEXT)

        training_label = "— descanso —" if is_rest else training.capitalize()
        training_font = muted_font if is_rest else body_font
        for i, line in enumerate(_wrap(draw, training_label, training_font, COL_TRAIN_W - 16)[:2]):
            draw.text((col_x[2], y + 10 + i * 22), line, font=training_font, fill=day_color)

        if not is_rest:
            for i, line in enumerate(_wrap(draw, food, body_font, COL_FOOD_W - 16)[:2]):
                draw.text((col_x[3], y + 10 + i * 22), line, font=body_font, fill=COLOR_TEXT)

        y += ROW_H
        draw.line([(MARGIN, y - 1), (WIDTH - MARGIN, y - 1)], fill=COLOR_HAIRLINE, width=1)

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
