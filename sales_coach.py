"""Programa de coaching comercial para Belén (Gerente de Ventas de I'AM).

TEO actúa como socio de ventas: comparte cada día un video curado de YouTube con una lección
aplicable a I'AM, propone estrategias a partir de los datos reales de la base, las itera con
Belén (cada versión queda guardada) y las convierte en un plan de 30 días con metas medibles.

Este módulo no depende de main.py: main lo configura con `configure()` (cliente del modelo y
números de staff a excluir de las métricas) y le pasa las tools propias de Belén que ya existían
(`confirmar_inscripcion`).
"""
import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta

import httpx

import storage
import youtube_coach
from scheduler import TIMEZONE, _send, _text_payload

logger = logging.getLogger("teo.coach")

TEMPLATE_CAPSULA_VENTAS = "teo_capsula_ventas"
COACH_HISTORY_LIMIT = 30
MAX_TOOL_ROUNDS = 4
PLAN_DAYS = 30
COACH_NAME = "Belén"
PILLAR_LABELS = {
    "ventas": "técnicas de venta",
    "objeciones": "manejo de objeciones",
    "duplicacion": "retención y duplicación de red",
}
EMPTY_REPLY = "Perdona, ¿me lo puedes decir de otra forma? Quiero asegurarme de entenderte bien."
NO_VIDEO_TEXT = "hoy no encontré un video que valga la pena; respóndeme y practicamos un caso real"

_llm = None
_exclude_phones: set = set()


def configure(llm, exclude_phones: set = None) -> None:
    """`llm`: coroutine con la firma de chat.completions.create (sin `model`)."""
    global _llm, _exclude_phones
    _llm = llm
    _exclude_phones = set(exclude_phones or ())


def _history_key(phone: str) -> str:
    # Clave propia para no mezclar el coaching con lo que Belén platique en "mi plan".
    return f"coach:{phone}"


def _today() -> date:
    return datetime.now(TIMEZONE).date()


def to_whatsapp(text: str) -> str:
    """El modelo a veces responde en Markdown; WhatsApp usa *negrita* y no muestra encabezados
    ni enlaces [texto](url). Se convierte para que el mensaje se lea limpio."""
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1: \2", text or "")
    text = re.sub(r"^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$", r"*\1*", text, flags=re.M)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"__(.+?)__", r"_\1_", text)
    text = re.sub(r"\*{2,}", "*", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _one_line(text: str, limit: int) -> str:
    """Los parámetros de plantillas de Meta no aceptan saltos de línea ni tabs ni >4 espacios."""
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

BELEN_COACH_PROMPT = """Eres TEO en su rol interno (NO de cara a clientes): socio estratégico y coach de \
ventas de Belén Figueroa, Gerente de Ventas de I'AM (suplementos y bienestar, venta directa / multinivel en \
México). No eres un asistente que solo obedece: eres un socio creativo que analiza los datos reales, propone, \
cuestiona con respeto y construye junto con ella. Hablas en español, con calidez y de tú.

LO QUE HACES CON BELÉN:
1) Cápsulas de aprendizaje: cada día hábil le llega un video de YouTube con una lección. Cuando hable de él, \
aterrízalo: qué técnica concreta enseña y cómo la aplicaría I'AM esta semana (guion, dinámica o mensaje listo \
para usar). Pregúntale si le sirvió. Cuando diga que un video le sirvió (o que no), llama SIEMPRE a \
`calificar_video` con el id de ese video (aparece como [id N] en los videos que ya le enviaste) — así TEO \
aprende qué tipo de contenido le funciona. Si pide más material de un tema, usa \
`buscar_video_ventas`.
2) Co-creación de estrategias. Método:
   a. Diagnóstico con datos: llama a `consultar_datos_ventas` y detecta la oportunidad más clara (ej. \
compradores sin recompra, cotizaciones atascadas, socios inactivos, prospectos que no responden).
   b. Propón 2 o 3 borradores cortos. Cada uno con esta estructura: Objetivo · Segmento (con su número real) · \
Mensaje o guion listo para copiar · Incentivo · Canal · Calendario · KPI · Riesgos.
   c. Pregúntale cuál le late y qué ajustaría. Itera con ella; no impongas.
   d. Cuando una versión le guste, guárdala con `guardar_estrategia` (si ya existe, pasa su id para que quede \
como versión nueva). Cambia su estado con `cambiar_estado_estrategia` cuando la apruebe, la termine o la \
descarte, y registra qué resultado dio — ese aprendizaje se reutiliza en las siguientes propuestas.
   e. Ofrécele convertir la estrategia aprobada en el plan de 30 días.
3) Plan de ejecución de 30 días (`crear_plan_30_dias`):
   - 2 a 4 metas medibles. Usa métricas automáticas cuando se pueda (TEO las mide solo): pedidos_autorizados, \
monto_vendido, inscripciones_confirmadas, cotizaciones, recompras, prospectos_reactivados. Para lo que TEO no \
ve (llamadas, reuniones, socios capacitados) usa "manual:<nombre>" y Belén reporta el avance.
   - Las metas salen del histórico real (consulta los datos antes); si no hay histórico suficiente, dilo y \
propón una línea base prudente para la primera semana.
   - 4 semanas de acciones concretas, cada una con responsable (Belén, Yeni, Armando, el equipo de \
distribuidores…) y una revisión semanal.
   - Antes de crearlo, muéstraselo resumido y confirma con ella. Solo hay un plan activo: crear uno nuevo \
cierra el anterior, avísale.
   - Para el seguimiento usa `ver_avance_plan` y `registrar_avance` (metas manuales y acciones hechas).

REGLAS:
- Solo usas números que vengan de las funciones. Nunca inventes cifras, casos ni "estadísticas del sector"; si \
mencionas una práctica de otras empresas, preséntala como práctica conocida, no como dato.
- Nunca escribas un enlace que no venga de una función.
- Guiones para clientes: sin promesas de curación ni afirmaciones de que un producto trata o cura \
enfermedades; habla de bienestar y apoyo a hábitos.
- Reclutamiento: sin promesas de ingresos garantizados ni de "dinero fácil"; el ingreso depende del trabajo \
y de las ventas reales.
- Persuasión ética: urgencia y escasez solo si son reales; nada de presión ni engaños. Respeta a quien no \
quiere recibir mensajes (consentimiento de seguimiento).
- Los teléfonos de clientes que te den las funciones son para que el equipo los contacte; no los repitas \
completos si no hace falta.
- Si Belén te dice que ya dio de alta a un nuevo socio, usa `confirmar_inscripcion` (teléfono del aspirante \
y tiempo en que se refleja); si falta el teléfono, pídeselo.
- Formato WhatsApp: mensajes breves, con viñetas, negritas con *asteriscos* y máximo ~15 líneas salvo que \
pida el detalle. Cierra con una pregunta o el siguiente paso concreto.
"""


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _tool(name: str, description: str, properties: dict, required: list = ()) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(required)},
        },
    }


_METRIC_DESC = (
    "Métrica: una automática (" + ", ".join(storage.AUTO_METRICS) + ") o 'manual:<nombre>' para lo que "
    "Belén reporta a mano."
)

COACH_TOOLS = [
    _tool("consultar_datos_ventas", "Foto comercial real de I'AM en los últimos N días: pedidos por estado, monto "
          "vendido, productos más vendidos, cotizaciones atascadas, candidatos a recompra, socios inactivos, "
          "interés en el negocio y seguimientos. Úsala antes de proponer estrategias o metas.",
          {"dias": {"type": "integer", "description": "Ventana en días (default 30)."}}),
    _tool("buscar_video_ventas", "Busca en YouTube un video útil sobre un tema de ventas (técnicas, objeciones, "
          "retención, duplicación) y devuelve el enlace real con su lección.",
          {"tema": {"type": "string", "description": "Tema o pregunta de Belén, en español."}}, ["tema"]),
    _tool("calificar_video", "Registra si un video le sirvió a Belén (aprendizaje para las siguientes cápsulas).",
          {"id_video": {"type": "integer"}, "util": {"type": "boolean"}, "nota": {"type": "string"}},
          ["id_video", "util"]),
    _tool("guardar_estrategia", "Guarda una estrategia co-creada. Con id_estrategia guarda una versión nueva de "
          "una existente (la anterior queda en el historial).",
          {
              "titulo": {"type": "string"},
              "tipo": {"type": "string", "enum": list(storage.STRATEGY_KINDS)},
              "contenido": {"type": "string", "description": "Texto completo: objetivo, segmento, guion, "
                            "incentivo, canal, calendario, KPI y riesgos."},
              "id_estrategia": {"type": "integer"},
          }, ["titulo", "tipo", "contenido"]),
    _tool("listar_estrategias", "Lista las estrategias guardadas.",
          {"incluir_cerradas": {"type": "boolean", "description": "Incluir terminadas y descartadas."}}),
    _tool("ver_estrategia", "Contenido completo de una estrategia.", {"id_estrategia": {"type": "integer"}},
          ["id_estrategia"]),
    _tool("cambiar_estado_estrategia", "Cambia el estado de una estrategia y opcionalmente registra su resultado.",
          {
              "id_estrategia": {"type": "integer"},
              "estado": {"type": "string", "enum": list(storage.STRATEGY_STATUSES)},
              "resultado": {"type": "string", "description": "Qué pasó al ejecutarla (aprendizaje)."},
          }, ["id_estrategia", "estado"]),
    _tool("crear_plan_30_dias", "Crea el plan de ejecución de 30 días (cierra el plan activo anterior). Solo "
          "después de que Belén lo confirme.",
          {
              "titulo": {"type": "string"},
              "id_estrategia": {"type": "integer", "description": "Estrategia que ejecuta el plan, si aplica."},
              "fecha_inicio": {"type": "string", "description": "YYYY-MM-DD; default hoy."},
              "metas": {"type": "array", "items": {"type": "object", "properties": {
                  "metrica": {"type": "string", "description": _METRIC_DESC},
                  "objetivo": {"type": "number"},
                  "responsable": {"type": "string"},
              }, "required": ["metrica", "objetivo"]}},
              "acciones": {"type": "array", "items": {"type": "object", "properties": {
                  "semana": {"type": "integer", "description": "1 a 4"},
                  "descripcion": {"type": "string"},
                  "responsable": {"type": "string"},
              }, "required": ["semana", "descripcion"]}},
          }, ["titulo", "metas", "acciones"]),
    _tool("registrar_avance", "Registra avance del plan activo: valor actual de una meta manual (id_meta + "
          "valor) y/o una acción hecha o pendiente (id_accion + hecha).",
          {
              "id_meta": {"type": "integer"}, "valor": {"type": "number"},
              "id_accion": {"type": "integer"}, "hecha": {"type": "boolean"},
          }),
    _tool("ver_avance_plan", "Avance real del plan activo: % por meta, ritmo esperado y acciones por semana.", {}),
]


def _metric_label(metric: str) -> str:
    if metric.startswith("manual:"):
        return metric.split(":", 1)[1].strip() or metric
    return storage.AUTO_METRICS.get(metric, metric)


def plan_progress(plan: dict = None, today: date = None) -> dict:
    """Avance del plan (activo por default): valor real por meta, % y ritmo esperado."""
    plan = plan or storage.get_active_plan()
    if not plan:
        return None
    today = today or _today()
    start, end = date.fromisoformat(plan["start_date"]), date.fromisoformat(plan["end_date"])
    elapsed = min(max((today - start).days + 1, 0), (end - start).days + 1)
    expected_pct = round(100 * elapsed / ((end - start).days + 1))
    goals = []
    for goal in plan["goals"]:
        if goal["metric"].startswith("manual:"):
            actual = goal["manual_value"] or 0
        else:
            actual = storage.get_metric_actual(goal["metric"], plan["start_date"], plan["end_date"], _exclude_phones)
        pct = round(100 * actual / goal["target"]) if goal["target"] else 0
        goals.append({**goal, "label": _metric_label(goal["metric"]), "actual": actual, "pct": pct})
    week = min(max((today - start).days // 7 + 1, 1), 4)
    return {
        "plan": plan, "goals": goals, "expected_pct": expected_pct, "day": elapsed, "week": week,
        "avg_pct": round(sum(g["pct"] for g in goals) / len(goals)) if goals else 0,
        "pending_actions": [a for a in plan["actions"] if a["status"] != "hecha"],
    }


def format_plan_progress(progress: dict) -> str:
    plan = progress["plan"]
    lines = [
        f"📋 *{plan['title']}* (día {progress['day']} de {PLAN_DAYS}, {plan['start_date']} → {plan['end_date']})",
        f"Ritmo esperado a hoy: {progress['expected_pct']}%",
    ]
    for goal in progress["goals"]:
        flag = "✅" if goal["pct"] >= 100 else ("🟢" if goal["pct"] >= progress["expected_pct"] else "🟠")
        owner = f" — {goal['owner']}" if goal.get("owner") else ""
        lines.append(f"{flag} [meta {goal['id']}] {goal['label']}: {goal['actual']:g} / {goal['target']:g} "
                     f"({goal['pct']}%){owner}")
    lines.append("Acciones:")
    for action in plan["actions"]:
        mark = "☑" if action["status"] == "hecha" else "☐"
        owner = f" ({action['owner']})" if action.get("owner") else ""
        lines.append(f"{mark} [acción {action['id']}] S{action['week']}: {action['description']}{owner}")
    return "\n".join(lines)


def plan_progress_line() -> str:
    """Una línea para el reporte ejecutivo de Alma (vacía si no hay plan activo)."""
    progress = plan_progress()
    if not progress:
        return ""
    main_goal = progress["goals"][0] if progress["goals"] else None
    goal_text = f" · {main_goal['label']}: {main_goal['actual']:g}/{main_goal['target']:g}" if main_goal else ""
    return (f"Plan de ventas '{progress['plan']['title']}': {progress['avg_pct']}% promedio "
            f"(esperado {progress['expected_pct']}%){goal_text}")


async def _llm_call(**kwargs):
    if _llm is None:
        raise RuntimeError("sales_coach no está configurado (falta configure()).")
    return await _llm(**kwargs)


CURATION_PROMPT = """Eres curador de contenido de capacitación comercial para I'AM (suplementos y bienestar, \
venta directa/multinivel en México). Tema del día: {pillar_desc}. Elige el video MÁS útil y aplicable para la \
Gerente de Ventas entre los candidatos. Descarta: ataques o críticas al multinivel, promesas de dinero fácil o \
ingresos garantizados, contenido de otro tema, clickbait vacío o de otra industria sin aplicación clara. \
Si ninguno es perfecto, elige el mejor aplicable (un video de otra empresa de venta directa sirve si su \
técnica se puede usar en I'AM); usa null solo si ninguno es aplicable o alguno de los anteriores aplica a todos.

Candidatos:
{candidates}

La lección es una sola oración (máximo 250 caracteres), práctica y concreta: empieza con un verbo en \
imperativo, nombra la técnica específica del video y di cómo usarla esta semana en I'AM (con un ejemplo de \
frase o acción). NO empieces con "El video enseña" ni hables de lo importante que es el tema. Ejemplo: \
"Cuando te digan 'está caro', divide el precio del protocolo entre 30 días y compáralo con un café diario; \
pruébalo esta semana con los prospectos en seguimiento."

Responde SOLO con JSON: {{"indice": <número del candidato o null si ninguno sirve>, "leccion": "<la lección>"}}"""


async def curate_video(candidates: list, pillar: str):
    """El modelo elige un candidato y escribe la lección. El enlace sale del candidato (de la
    API), nunca del texto del modelo. Devuelve (video, lección) o None."""
    if not candidates:
        return None
    listing = "\n".join(
        f"{i}. {c['title']} — {c['channel']} ({c['minutes']} min, {c['views']:,} vistas). {c['description'][:200]}"
        for i, c in enumerate(candidates[:6], 1)
    )
    completion = await _llm_call(
        messages=[{"role": "system", "content": CURATION_PROMPT.format(
            pillar_desc=youtube_coach.PILLARS.get(pillar, pillar), candidates=listing)}],
        temperature=0.2, max_tokens=250,
    )
    raw = completion.choices[0].message.content or ""
    match = re.search(r"\{.*\}", raw, re.S)
    try:
        choice = json.loads(match.group(0)) if match else {}
        index = choice.get("indice")
        lesson = _one_line(str(choice.get("leccion") or ""), 300)
    except (ValueError, AttributeError):
        logger.warning("Curaduría con respuesta no válida: %s", raw[:200])
        return None
    if not isinstance(index, int) or not 1 <= index <= min(len(candidates), 6) or not lesson:
        return None
    return candidates[index - 1], lesson


async def find_capsule(http_client, pillar: str, queries: list) -> dict:
    """Busca y curaduría un video nuevo para `pillar` probando las `queries` en orden (máx. 2,
    para cuidar la cuota). Devuelve el recurso guardado, o None si YouTube no está disponible o
    ningún video pasó la curaduría."""
    for query in queries[:2]:
        try:
            candidates = await youtube_coach.find_candidates(http_client, query, storage.coach_video_known)
        except youtube_coach.YouTubeUnavailable as exc:
            logger.warning("YouTube no disponible para el coaching de ventas: %s", exc)
            return None
        picked = await curate_video(candidates, pillar)
        if picked:
            video, lesson = picked
            resource_id = storage.save_coach_resource(video, pillar, query, lesson)
            return storage.get_coach_resource(resource_id)
    return None


# ---------------------------------------------------------------------------
# Handlers de las tools
# ---------------------------------------------------------------------------

async def _t_sales_data(http_client, dias: int = 30, **_):
    snapshot = storage.get_sales_snapshot(int(dias or 30), _exclude_phones)
    return "OK: datos reales (no inventes otros):\n" + json.dumps(snapshot, ensure_ascii=False)


async def _t_search_video(http_client, tema: str = "", **_):
    pillar = youtube_coach.pillar_for_topic(tema)
    queries = [q for q in [tema.strip()] if q] + youtube_coach.SEARCH_TOPICS[pillar][:1]
    resource = await find_capsule(http_client, pillar, queries)
    if not resource:
        return ("OK: no encontré un video que valga la pena ahora (o YouTube no está disponible). No inventes "
                "enlaces; ofrece trabajar el tema directamente con ella.")
    storage.mark_coach_resource_sent(resource["id"])
    return (f"OK: video [id {resource['id']}] \"{resource['title']}\" ({resource['channel']}): {resource['url']}\n"
            f"Lección: {resource['lesson']}\nComparte el enlace tal cual y aterrízalo a I'AM.")


async def _t_rate_video(http_client, id_video: int = None, util: bool = True, nota: str = None, **_):
    if not storage.rate_coach_resource(int(id_video), bool(util), nota):
        return f"ERROR: no existe el video {id_video}."
    return "OK: calificación guardada; se usará para elegir los siguientes videos."


async def _t_save_strategy(http_client, titulo: str = "", tipo: str = "otra", contenido: str = "",
                           id_estrategia: int = None, **_):
    if not contenido.strip():
        return "ERROR: falta el contenido de la estrategia."
    strategy = storage.save_strategy(titulo, tipo, contenido, int(id_estrategia) if id_estrategia else None)
    if not strategy:
        return f"ERROR: no existe la estrategia {id_estrategia}."
    return f"OK: estrategia [id {strategy['id']}] \"{strategy['title']}\" guardada (versión {strategy['version']})."


async def _t_list_strategies(http_client, incluir_cerradas: bool = False, **_):
    rows = storage.list_strategies(bool(incluir_cerradas))
    if not rows:
        return "OK: no hay estrategias guardadas todavía."
    return "OK: estrategias:\n" + "\n".join(
        f"- [id {s['id']}] {s['title']} ({s['kind']}, {s['status']}, v{s['version']})"
        + (f" — resultado: {s['result']}" if s.get("result") else "")
        for s in rows
    )


async def _t_view_strategy(http_client, id_estrategia: int = None, **_):
    strategy = storage.get_strategy(int(id_estrategia))
    if not strategy:
        return f"ERROR: no existe la estrategia {id_estrategia}."
    return (f"OK: [id {strategy['id']}] {strategy['title']} ({strategy['kind']}, {strategy['status']}, "
            f"v{strategy['version']})\n{strategy['content']}"
            + (f"\nResultado: {strategy['result']}" if strategy.get("result") else ""))


async def _t_strategy_status(http_client, id_estrategia: int = None, estado: str = "", resultado: str = None, **_):
    if estado not in storage.STRATEGY_STATUSES:
        return f"ERROR: estado inválido; usa uno de {', '.join(storage.STRATEGY_STATUSES)}."
    if not storage.set_strategy_status(int(id_estrategia), estado, resultado):
        return f"ERROR: no existe la estrategia {id_estrategia}."
    return f"OK: estrategia {id_estrategia} ahora está '{estado}'."


def _valid_metric(metric: str) -> bool:
    return metric in storage.AUTO_METRICS or (metric.startswith("manual:") and len(metric) > len("manual:"))


async def _t_create_plan(http_client, titulo: str = "", metas: list = None, acciones: list = None,
                         id_estrategia: int = None, fecha_inicio: str = None, **_):
    metas, acciones = metas or [], acciones or []
    if not metas or not acciones:
        return "ERROR: el plan necesita al menos una meta y una acción."
    goals = []
    for meta in metas:
        metric = str(meta.get("metrica", "")).strip()
        if not _valid_metric(metric):
            return f"ERROR: métrica '{metric}' inválida. {_METRIC_DESC}"
        try:
            target = float(meta.get("objetivo"))
        except (TypeError, ValueError):
            return f"ERROR: la meta '{metric}' necesita un objetivo numérico."
        if target <= 0:
            return f"ERROR: el objetivo de '{metric}' debe ser mayor a 0."
        goals.append({"metric": metric, "target": target, "owner": meta.get("responsable")})
    try:
        start = date.fromisoformat(fecha_inicio) if fecha_inicio else _today()
    except ValueError:
        return "ERROR: fecha_inicio debe ser YYYY-MM-DD."
    end = start + timedelta(days=PLAN_DAYS - 1)
    actions = []
    for accion in acciones:
        week = min(max(int(accion.get("semana") or 1), 1), 4)
        actions.append({
            "week": week, "description": str(accion.get("descripcion", "")).strip(),
            "owner": accion.get("responsable"),
            "due_date": (start + timedelta(days=7 * week - 1)).isoformat(),
        })
    if id_estrategia and not storage.get_strategy(int(id_estrategia)):
        return f"ERROR: no existe la estrategia {id_estrategia}."
    had_plan = storage.get_active_plan() is not None
    storage.create_sales_plan(titulo, start.isoformat(), end.isoformat(), goals, actions,
                              int(id_estrategia) if id_estrategia else None)
    note = " (el plan anterior quedó cerrado)" if had_plan else ""
    return "OK: plan creado" + note + ".\n" + format_plan_progress(plan_progress())


async def _t_register_progress(http_client, id_meta: int = None, valor: float = None, id_accion: int = None,
                               hecha: bool = None, **_):
    plan = storage.get_active_plan()
    if not plan:
        return "ERROR: no hay plan activo."
    done = []
    if id_meta is not None:
        goal = next((g for g in plan["goals"] if g["id"] == int(id_meta)), None)
        if not goal:
            return f"ERROR: la meta {id_meta} no es del plan activo."
        if not goal["metric"].startswith("manual:"):
            return f"ERROR: la meta {id_meta} es automática; TEO la mide solo con los datos."
        if valor is None:
            return "ERROR: falta el valor actual de la meta."
        storage.set_goal_manual_value(int(id_meta), float(valor))
        done.append(f"meta {id_meta} = {float(valor):g}")
    if id_accion is not None:
        if not any(a["id"] == int(id_accion) for a in plan["actions"]):
            return f"ERROR: la acción {id_accion} no es del plan activo."
        storage.set_action_status(int(id_accion), hecha is not False)
        done.append(f"acción {id_accion} {'hecha' if hecha is not False else 'pendiente'}")
    if not done:
        return "ERROR: indica id_meta + valor o id_accion + hecha."
    return "OK: " + ", ".join(done) + ".\n" + format_plan_progress(plan_progress())


async def _t_view_progress(http_client, **_):
    progress = plan_progress()
    if not progress:
        return "OK: no hay plan activo. Ofrécele construir uno a partir de una estrategia."
    return "OK:\n" + format_plan_progress(progress)


COACH_DISPATCH = {
    "consultar_datos_ventas": _t_sales_data,
    "buscar_video_ventas": _t_search_video,
    "calificar_video": _t_rate_video,
    "guardar_estrategia": _t_save_strategy,
    "listar_estrategias": _t_list_strategies,
    "ver_estrategia": _t_view_strategy,
    "cambiar_estado_estrategia": _t_strategy_status,
    "crear_plan_30_dias": _t_create_plan,
    "registrar_avance": _t_register_progress,
    "ver_avance_plan": _t_view_progress,
}


# ---------------------------------------------------------------------------
# Conversación con Belén
# ---------------------------------------------------------------------------

def context_note() -> str:
    """Contexto que se inyecta en cada turno: plan activo, aprendizaje acumulado y videos recientes."""
    parts = [f"Hoy es {_today().isoformat()}."]
    progress = plan_progress()
    parts.append(format_plan_progress(progress) if progress else "No hay plan de 30 días activo.")
    learning = storage.get_coach_learning()
    parts.append("Aprendizaje acumulado: " + json.dumps(learning, ensure_ascii=False))
    recent = storage.list_recent_coach_resources(3)
    if recent:
        parts.append("Videos que ya le enviaste (id, título, lección): " + "; ".join(
            f"[{r['id']}] {r['title']} — {r['lesson']}" for r in recent))
    return "\n".join(parts)


def _assistant_with_tools(message) -> dict:
    return {
        "role": "assistant",
        "content": message.content,
        "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in message.tool_calls
        ],
    }


async def _run_tool(http_client, tool_call, dispatch: dict) -> str:
    name = tool_call.function.name
    handler = dispatch.get(name)
    if not handler:
        return f"ERROR: función '{name}' desconocida."
    try:
        args = json.loads(tool_call.function.arguments or "{}") or {}
        return str(await handler(http_client, **args))
    except Exception:
        logger.exception("La función de coaching '%s' falló.", name)
        return "ERROR: la función falló internamente. Continúa sin ella y no digas que se hizo."


async def reply_to(http_client, phone: str, text: str, extra_tools: list = (), extra_dispatch: dict = None) -> str:
    """Genera la respuesta de TEO a Belén con memoria persistente (sobrevive reinicios) y
    varias rondas de tools. No envía nada a WhatsApp."""
    key = _history_key(phone)
    history = [{"role": row["role"], "content": row["content"]}
               for row in storage.get_conversation(key, COACH_HISTORY_LIMIT)]
    storage.log_message(key, "user", text)
    tools = COACH_TOOLS + list(extra_tools)
    dispatch = {**COACH_DISPATCH, **(extra_dispatch or {})}
    messages = [
        {"role": "system", "content": BELEN_COACH_PROMPT},
        {"role": "system", "content": context_note()},
        *history,
        {"role": "user", "content": text},
    ]

    reply = EMPTY_REPLY
    for round_number in range(1, MAX_TOOL_ROUNDS + 2):
        extra = {"tools": tools} if round_number <= MAX_TOOL_ROUNDS else {}
        completion = await _llm_call(messages=messages, temperature=0.5, max_tokens=1200, **extra)
        message = completion.choices[0].message
        if not message.tool_calls:
            reply = to_whatsapp(message.content or "") or EMPTY_REPLY
            break
        messages.append(_assistant_with_tools(message))
        for tool_call in message.tool_calls:
            messages.append({"role": "tool", "tool_call_id": tool_call.id,
                             "content": await _run_tool(http_client, tool_call, dispatch)})
    storage.log_message(key, "assistant", reply)
    return reply


# ---------------------------------------------------------------------------
# Cápsula diaria (lun–vie)
# ---------------------------------------------------------------------------

async def _send_text(http_client, api_url, token, phone, text) -> bool:
    payload = {"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": text[:4096]}}
    return await _send(http_client, api_url, token, payload, phone, "capsula_ventas_texto")


async def send_daily_capsule(http_client, api_url: str, token: str, phone: str, today: date = None) -> bool:
    """Busca el video del día (o un respaldo bien calificado), lo manda con la plantilla
    teo_capsula_ventas (Belén puede no haber escrito en 24 h) y, si la plantilla falla, intenta
    texto libre. Los lunes agrega el avance del plan activo. Nunca inventa un enlace."""
    today = today or _today()
    pillar, query = youtube_coach.query_for_day(today.toordinal())
    others = [q for q in youtube_coach.SEARCH_TOPICS[pillar] if q != query]
    resource = await find_capsule(http_client, pillar, [query, *others[:1]])
    if not resource:
        resource = storage.get_fallback_coach_resource()
        if resource:
            pillar = resource.get("pillar") or pillar

    lesson = resource["lesson"] if resource else (
        "Elige una objeción que escuchaste esta semana y escríbeme cómo la respondiste; la mejoramos juntos.")
    progress = plan_progress(today=today) if today.weekday() == 0 else None
    if progress:
        lesson = (f"Avance del plan '{progress['plan']['title']}': {progress['avg_pct']}% "
                  f"(esperado {progress['expected_pct']}%), {len(progress['pending_actions'])} acciones pendientes. "
                  f"{lesson}")
    params = [
        COACH_NAME,
        PILLAR_LABELS.get(pillar, pillar),
        _one_line(lesson, 600),
        resource["url"] if resource else NO_VIDEO_TEXT,
    ]
    sent = await _send(http_client, api_url, token, _text_payload(phone, TEMPLATE_CAPSULA_VENTAS, params),
                       phone, "capsula_ventas")
    body = (f"Hola {params[0]}, aquí va tu cápsula de ventas de hoy sobre {params[1]}.\n\n{params[2]}\n\n"
            + (f"🎥 {resource['title']}: {resource['url']}\n\n" if resource else "")
            + "Respóndeme y lo aterrizamos juntos para I'AM.")
    if not sent:
        sent = await _send_text(http_client, api_url, token, phone, body)
    if progress and sent:
        await _send_text(http_client, api_url, token, phone, format_plan_progress(progress))
    if sent:
        if resource:
            storage.mark_coach_resource_sent(resource["id"])
        storage.log_coach_daily(today.isoformat(), resource["id"] if resource else None)
        storage.log_message(_history_key(phone), "assistant", body)
    return sent


async def run_coach_loop(http_client: httpx.AsyncClient, api_url: str, token: str, phone: str,
                         hour: int = 9, minute: int = 0) -> None:
    """Lun–vie a la hora configurada manda la cápsula a Belén. El registro en BD evita
    duplicados tras un reinicio; si el envío falla, no reintenta hasta el día siguiente."""
    logger.info("Loop de coaching de ventas iniciado (%02d:%02d lun–vie).", hour, minute)
    if not youtube_coach.get_api_key():
        logger.warning("YOUTUBE_API_KEY no está configurada: las cápsulas usarán solo videos ya calificados "
                       "o un ejercicio sin enlace.")
    attempted = None
    while True:
        try:
            now = datetime.now(TIMEZONE)
            today = now.date()
            if (now.weekday() < 5 and (now.hour, now.minute) >= (hour, minute) and now.hour < hour + 3
                    and attempted != today and not storage.coach_daily_sent_on(today.isoformat())):
                attempted = today
                await send_daily_capsule(http_client, api_url, token, phone, today)
        except Exception:
            logger.exception("Error inesperado en el loop de coaching de ventas.")
        await asyncio.sleep(30)
