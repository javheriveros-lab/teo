"""Matriz de videos oficiales de I'AM (YouTube @iam-yosoy5878 y TikTok @iam_salud) y búsqueda por
protocolo, producto o malestar, para que TEO comparta el video exacto que le corresponde a cada caso
en vez de inventar enlaces. Se edita a mano cuando se publican videos nuevos."""

import re

from product_images import _normalize

# Cada entrada: url, plataforma, titulo, temas y (opcional) testimonio=True. En `temas` van los
# nombres EXACTOS del catálogo del SYSTEM_PROMPT (protocolo/combo y productos) y los malestares que
# el video explica — la búsqueda compara contra estos temas, no contra el título.
#
# Curaduría de cumplimiento: quedan fuera, a propósito, los testimonios de cáncer, VIH, COVID,
# diabetes y parálisis facial, los videos cuyo título dice "cáncer", "tratamiento" o "curar", el de
# telomerasa (su descripción en YouTube dice "cura de cáncer"), y los de negocio y eventos, que no
# responden a un malestar.
VIDEO_LIBRARY = [
    # --- YouTube: producto y educación ---
    {
        "url": "https://youtu.be/NoH0QjIlBds",
        "plataforma": "YouTube",
        "titulo": "Paquete Detox de I'AM: qué incluye y para quién es",
        "temas": [
            "Combo Detox", "Sheyck Clorofila", "Bisxantone", "Colon Light", "Fiber Tabs",
            "detox", "limpieza", "estreñimiento", "falta de energía", "control de peso",
        ],
    },
    {
        "url": "https://youtu.be/YqWuqcPTqiU",
        "plataforma": "YouTube",
        "titulo": "Protocolo de apoyo glucémico de I'AM: sus 5 productos",
        "temas": [
            "Protocolo Control Glucémico", "Power Milk", "Sheyck Clorofila", "Hecen Mangosteen",
            "Sweet Free", "Memory Kids", "diabetes", "glucosa",
        ],
    },
    {
        "url": "https://youtu.be/8cP6-GbFelY",
        "plataforma": "YouTube",
        "titulo": "Bisxantone: qué es este antioxidante",
        "temas": ["Bisxantone", "antioxidante", "inflamación"],
    },
    {
        "url": "https://youtube.com/shorts/gcAMPTmLQD0",
        "plataforma": "YouTube",
        "titulo": "Clorofila: qué aporta a tu digestión",
        "temas": ["Sheyck Clorofila", "clorofila", "digestión"],
    },
    {
        "url": "https://youtube.com/shorts/kI9Sa6EBIwM",
        "plataforma": "YouTube",
        "titulo": "Fiber Tabs en 7 segundos",
        "temas": ["Fiber Tabs", "fibra"],
    },
    {
        "url": "https://youtube.com/shorts/9HeSIRZAYk4",
        "plataforma": "YouTube",
        "titulo": "Menopausia y flora intestinal: por qué cuidarla",
        "temas": ["Biotic", "probiótico", "microbiota", "flora intestinal", "menopausia"],
    },
    {
        "url": "https://youtu.be/Lc4dnGAaPQg",
        "plataforma": "YouTube",
        "titulo": "Por qué cuidar tu colon (plática del Dr. Joel Robbins)",
        "temas": [
            "Combo Salud de Colon", "Combo Reset Digestivo", "Protocolo Fluidez Digestiva",
            "colon", "estreñimiento", "digestión",
        ],
    },
    {
        "url": "https://youtu.be/eAm4oBf40Jo",
        "plataforma": "YouTube",
        "titulo": "Antioxidantes: cómo ayudan a tu piel y tu cabello",
        "temas": [
            "Protocolo Estética Estructural", "Combo Armonización Dérmica", "Combo Longevidad Celular",
            "piel", "cabello", "antioxidantes", "envejecimiento",
        ],
    },
    {
        "url": "https://youtu.be/_dSPJSA_auk",
        "plataforma": "YouTube",
        "titulo": "Cómo funcionan los fitonutrientes en tus células",
        "temas": ["fitonutrientes", "nutrición celular", "optimización biológica", "cómo funcionan los suplementos"],
    },
    {
        "url": "https://youtu.be/hFK-gWJj-gc",
        "plataforma": "YouTube",
        "titulo": "Niños que toman productos I'AM",
        "temas": ["Combo Desarrollo Escolar", "Combo Crecimiento Infantil", "niños", "programa escolar"],
    },
    # --- YouTube: testimonios (experiencias personales, sin enfermedades graves) ---
    {
        "url": "https://youtu.be/cKFIQfiDYwI",
        "plataforma": "YouTube",
        "titulo": "Testimonio: perdió el cabello y así lo tiene hoy",
        "temas": ["Protocolo Estética Estructural", "cabello", "caída de cabello"],
        "testimonio": True,
    },
    {
        "url": "https://youtu.be/368SflOy31s",
        "plataforma": "YouTube",
        "titulo": "Testimonio: problemas gastrointestinales",
        "temas": ["Combo Reset Digestivo", "Combo Restauración Digestiva", "problemas gastrointestinales", "digestión"],
        "testimonio": True,
    },
    {
        "url": "https://youtube.com/shorts/qZtw4rhuPHs",
        "plataforma": "YouTube",
        "titulo": "Testimonio: dolores de cabeza intensos",
        "temas": ["Combo Migraña", "migraña", "dolor de cabeza"],
        "testimonio": True,
    },
    {
        "url": "https://youtu.be/Fffvmkj2lU4",
        "plataforma": "YouTube",
        "titulo": "Testimonio: comía tierra por falta de nutrientes",
        "temas": ["Combo Anemia", "Combo Asimilación de Nutrientes", "anemia", "falta de nutrientes"],
        "testimonio": True,
    },
    {
        "url": "https://youtu.be/YkPbQCKC_Eo",
        "plataforma": "YouTube",
        "titulo": "Testimonio: antes no podía caminar",
        "temas": ["falta de energía", "dolor de cuerpo", "cansancio"],
        "testimonio": True,
    },
    # --- TikTok @iam_salud ---
    {
        "url": "https://www.tiktok.com/@iam_salud/video/7641775726332628244",
        "plataforma": "TikTok",
        "titulo": "Energía mental sin depender del café",
        "temas": ["Vitality Upgrade System", "falta de energía", "fatiga", "concentración", "energía mental"],
    },
    {
        "url": "https://www.tiktok.com/@iam_salud/video/7644374556093861140",
        "plataforma": "TikTok",
        "titulo": "Probióticos: el nuevo aliado de tu digestión",
        "temas": ["Biotic", "probiótico", "digestión", "mala digestión"],
    },
    {
        "url": "https://www.tiktok.com/@iam_salud/video/7439194735694187831",
        "plataforma": "TikTok",
        "titulo": "Skincare para principiantes",
        "temas": ["piel", "skincare", "cuidado de la piel"],
    },
    {
        "url": "https://www.tiktok.com/@iam_salud/video/7653310418181442837",
        "plataforma": "TikTok",
        "titulo": "El café no te da energía real",
        "temas": ["Vitality Upgrade System", "falta de energía", "café"],
    },
    {
        "url": "https://www.tiktok.com/@iam_salud/video/7652159216756460820",
        "plataforma": "TikTok",
        "titulo": "La optimización biológica no es de pocos",
        "temas": ["optimización biológica", "biohacking"],
    },
    {
        "url": "https://www.tiktok.com/@iam_salud/video/7649961973869841685",
        "plataforma": "TikTok",
        "titulo": "Biohacking para diabetes tipo 2",
        "temas": ["Protocolo Control Glucémico", "diabetes", "glucosa", "carbohidratos"],
    },
    {
        "url": "https://www.tiktok.com/@iam_salud/video/7649607615319756052",
        "plataforma": "TikTok",
        "titulo": "Tipos de diabetes, explicados",
        "temas": ["Protocolo Control Glucémico", "diabetes", "azúcar en la sangre"],
    },
    {
        "url": "https://www.tiktok.com/@iam_salud/video/7648058633338637588",
        "plataforma": "TikTok",
        "titulo": "Si tienes diabetes, mira esto",
        "temas": ["Protocolo Control Glucémico", "diabetes", "glucosa", "dieta"],
    },
    {
        "url": "https://www.tiktok.com/@iam_salud/video/7647677502164356369",
        "plataforma": "TikTok",
        "titulo": "3 estafas comunes en suplementos",
        "temas": ["estafas", "desconfianza", "cómo elegir suplementos", "suplementos confiables"],
    },
    {
        "url": "https://www.tiktok.com/@iam_salud/video/7647379127384755473",
        "plataforma": "TikTok",
        "titulo": "El mito de la resistencia a la insulina",
        "temas": ["Protocolo Control Glucémico", "resistencia a la insulina", "insulina"],
    },
    {
        "url": "https://www.tiktok.com/@iam_salud/video/7646883837372386581",
        "plataforma": "TikTok",
        "titulo": "La ansiedad por el dulce no es falta de voluntad",
        "temas": ["ansiedad por el dulce", "antojos", "metabolismo"],
    },
    {
        "url": "https://www.tiktok.com/@iam_salud/video/7646612445875768597",
        "plataforma": "TikTok",
        "titulo": "El antioxidante más poderoso está en ti",
        "temas": ["Combo Longevidad Celular", "antioxidantes", "envejecimiento"],
    },
    {
        "url": "https://www.tiktok.com/@iam_salud/video/7645124048120220946",
        "plataforma": "TikTok",
        "titulo": "El estrés crónico sabotea tus metas",
        "temas": ["Protocolo Estabilización Neuro-Emocional", "estrés", "cortisol"],
    },
    {
        "url": "https://www.tiktok.com/@iam_salud/video/7642178025437728020",
        "plataforma": "TikTok",
        "titulo": "Hipertrofia definitiva con I'AM",
        "temas": ["Combo Hipertrofia", "masa muscular", "ganancia muscular"],
    },
    {
        "url": "https://www.tiktok.com/@iam_salud/video/7592006858668346645",
        "plataforma": "TikTok",
        "titulo": "Cuida tu microbiota intestinal",
        "temas": ["Biotic", "microbiota", "intestino", "digestión"],
    },
]

_ALLOWED_URL = re.compile(r"https://(www\.|m\.)?(youtube\.com|youtu\.be|tiktok\.com)/\S+")

for _video in VIDEO_LIBRARY:
    if not _ALLOWED_URL.fullmatch(_video["url"]):
        raise ValueError(f"URL de video no permitida en VIDEO_LIBRARY: {_video['url']}")
    if not _video.get("temas"):
        raise ValueError(f"El video {_video['url']} no tiene temas; no se podría recomendar.")

_EXACT_SCORE = 3
_PARTIAL_SCORE = 1


def find_videos(*terminos: str, limit: int = 2) -> list:
    """Videos más relevantes para los términos dados, en orden de prioridad: el primer término
    (normalmente el protocolo) pesa más que los siguientes (productos, malestar). Coincidencia
    exacta con un tema vale más que una parcial (ej. "bisxantone" dentro de "Bisxantone PET")."""
    queries = [_normalize(t) for t in terminos if t and _normalize(t)]
    if not queries:
        return []

    scored = []
    for index, video in enumerate(VIDEO_LIBRARY):
        temas = [_normalize(t) for t in video["temas"]]
        score = 0
        for position, query in enumerate(queries):
            weight = len(queries) - position
            if query in temas:
                score += _EXACT_SCORE * weight
            elif any(query in tema or tema in query for tema in temas):
                score += _PARTIAL_SCORE * weight
        if score:
            # Desempate: primero el video explicativo sobre el testimonio, luego el más específico
            # (menos temas), luego el que aparece primero.
            scored.append((score, not video.get("testimonio"), -len(temas), -index, video))

    scored.sort(key=lambda item: item[:4], reverse=True)
    return [video for *_, video in scored[:limit]]
