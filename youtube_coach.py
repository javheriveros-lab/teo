"""Búsqueda de videos de YouTube para el programa de coaching comercial de Belén (Ventas).

La clave se lee SOLO de la variable de entorno YOUTUBE_API_KEY, en cada llamada (así una clave
agregada en Railway funciona tras el redeploy sin tocar código). Nunca se escribe en logs ni en
mensajes de error: las URLs que la contienen se limpian antes de propagar cualquier falla.

Los enlaces que TEO comparte salen siempre de la respuesta de la API, nunca del modelo.
"""
import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger("teo.youtube")

API_BASE = "https://www.googleapis.com/youtube/v3"
MIN_VIEWS = 1500  # los temas de nicho (socios inactivos, duplicación) rara vez pasan de unos miles
MIN_SECONDS = 4 * 60
MAX_SECONDS = 40 * 60
MAX_AGE_DAYS = 3 * 365
SEARCH_RESULTS = 8

PILLARS = {
    "ventas": "técnicas de venta en redes de mercadeo y suplementos",
    "objeciones": "psicología de ventas y manejo de objeciones en salud y bienestar",
    "duplicacion": "retención de clientes y duplicación de redes",
}

SEARCH_TOPICS = {
    "ventas": [
        "cómo vender suplementos alimenticios en redes de mercadeo",
        "técnicas de venta network marketing productos de salud",
        "cómo vender por WhatsApp productos naturales",
        "venta consultiva bienestar suplementos",
        "casos de éxito venta directa nutrición",
    ],
    "objeciones": [
        "manejo de objeciones ventas está caro productos de salud",
        "psicología de ventas cierre de ventas venta directa",
        "cómo responder objeciones en network marketing",
        "objeciones más comunes al vender suplementos",
        "persuasión ética en ventas de bienestar",
    ],
    "duplicacion": [
        "duplicación en network marketing sistema",
        "cómo retener clientes en venta directa recompra",
        "liderazgo de equipos en redes de mercadeo",
        "cómo activar socios inactivos multinivel",
        "seguimiento a clientes y recompra en venta directa",
    ],
}
PILLAR_ORDER = ("ventas", "objeciones", "duplicacion")


class YouTubeUnavailable(Exception):
    """No hay clave, se acabó la cuota o la API falló. El mensaje nunca incluye la clave."""


def get_api_key():
    key = (os.environ.get("YOUTUBE_API_KEY") or "").strip()
    return key or None


def pillar_for_day(day_index: int) -> str:
    return PILLAR_ORDER[day_index % len(PILLAR_ORDER)]


def query_for_day(day_index: int) -> tuple:
    """(pilar, búsqueda) para el día `day_index` (ej. número de día del año): rota por pilar
    cada día y por búsqueda dentro del pilar cada vuelta."""
    pillar = pillar_for_day(day_index)
    topics = SEARCH_TOPICS[pillar]
    return pillar, topics[(day_index // len(PILLAR_ORDER)) % len(topics)]


def pillar_for_topic(topic: str) -> str:
    text = (topic or "").lower()
    if any(word in text for word in ("objec", "psicolog", "cierre", "caro", "persua")):
        return "objeciones"
    if any(word in text for word in ("duplic", "reten", "recompra", "equipo", "inactiv", "lider")):
        return "duplicacion"
    return "ventas"


def parse_duration(iso: str) -> int:
    """'PT1H2M3S' -> 3723 segundos (0 si no se puede leer)."""
    match = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not match:
        return 0
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _clean(text: str, key: str) -> str:
    return (text or "").replace(key, "***") if key else (text or "")


async def _get(http_client, endpoint: str, params: dict, key: str) -> dict:
    try:
        response = await http_client.get(f"{API_BASE}/{endpoint}", params={**params, "key": key}, timeout=20.0)
    except httpx.HTTPError as exc:
        raise YouTubeUnavailable(f"Error de red con YouTube ({type(exc).__name__}).") from None
    if response.status_code != 200:
        reason = ""
        try:
            error = response.json().get("error", {})
            details = [d.get("reason") for d in error.get("details") or [] if d.get("reason")]
            reason = details[0] if details else (error.get("errors") or [{}])[0].get("reason", "")
        except Exception:
            pass
        raise YouTubeUnavailable(_clean(f"YouTube respondió {response.status_code} {reason}".strip(), key))
    return response.json()


async def find_candidates(http_client, query: str, is_known=lambda video_id: False, api_key: str = None) -> list:
    """Busca videos en español para `query` y devuelve los que pasan los filtros de calidad
    (vistas, duración, no enviados antes), ordenados por vistas. Cuesta ~101 unidades de cuota.
    Lanza YouTubeUnavailable si no hay clave o la API falla."""
    key = api_key or get_api_key()
    if not key:
        raise YouTubeUnavailable("Falta la variable de entorno YOUTUBE_API_KEY.")

    published_after = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    search = await _get(http_client, "search", {
        "part": "snippet", "q": query, "type": "video", "maxResults": SEARCH_RESULTS,
        "relevanceLanguage": "es", "regionCode": "MX", "videoDuration": "medium",
        "publishedAfter": published_after, "safeSearch": "strict",
    }, key)
    snippets = {}
    for item in search.get("items", []):
        video_id = (item.get("id") or {}).get("videoId")
        if video_id and not is_known(video_id):
            snippets[video_id] = item.get("snippet") or {}
    if not snippets:
        return []

    details = await _get(http_client, "videos", {
        "part": "statistics,contentDetails", "id": ",".join(snippets),
    }, key)
    candidates = []
    for item in details.get("items", []):
        video_id = item.get("id")
        views = int((item.get("statistics") or {}).get("viewCount") or 0)
        seconds = parse_duration((item.get("contentDetails") or {}).get("duration"))
        if video_id not in snippets or views < MIN_VIEWS or not (MIN_SECONDS <= seconds <= MAX_SECONDS):
            continue
        snippet = snippets[video_id]
        candidates.append({
            "video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "title": html.unescape(snippet.get("title", "")).strip(),
            "channel": html.unescape(snippet.get("channelTitle", "")).strip(),
            "description": html.unescape(snippet.get("description") or "").strip()[:400],
            "views": views,
            "minutes": round(seconds / 60),
        })
    return sorted(candidates, key=lambda video: -video["views"])
