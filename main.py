import asyncio
import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import PlainTextResponse
from groq import AsyncGroq, BadRequestError, RateLimitError

import plans
import shipping
import storage
import vision
from calendar_image import render_catalog_infographic, render_weekly_calendar
from exercises import get_exercises_for_focus
from scheduler import TIMEZONE, run_reminder_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("teo")

# --- Configuración de entorno ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
WEBHOOK_VERIFY_TOKEN = os.environ.get("WEBHOOK_VERIFY_TOKEN")
GRAPH_API_VERSION = os.environ.get("META_GRAPH_API_VERSION", "v21.0")

for _name, _value in {
    "GROQ_API_KEY": GROQ_API_KEY,
    "WHATSAPP_TOKEN": WHATSAPP_TOKEN,
    "PHONE_NUMBER_ID": PHONE_NUMBER_ID,
    "WEBHOOK_VERIFY_TOKEN": WEBHOOK_VERIFY_TOKEN,
}.items():
    if not _value:
        logger.warning("Variable de entorno '%s' no está configurada.", _name)

# Requisito de negocio: el modelo queda fijo, no se expone como configurable.
# Nota: llama-3.1-8b-instant fue retirado del catálogo de Groq (404 model_not_found).
GROQ_MODEL = "openai/gpt-oss-20b"
WHATSAPP_API_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/messages"
WHATSAPP_MEDIA_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/media"
WHATSAPP_MAX_MESSAGE_LENGTH = 4096

# Módulo de Pagos: datos bancarios oficiales (texto fijo, nunca generado por el modelo para
# evitar errores de dígitos).
BANK_DETAILS_TEXT = (
    "Razón Social: CAMINO DE LA SALUD Y EL ÉXITO SAPI DE CV\n\n"
    "BANORTE\nCuenta: 0323187686\nCLABE Interbancaria: 072180003231876864\n\n"
    "BANAMEX\nSucursal: 7014\nCuenta: 1442080\nCLABE Interbancaria: 002180701414420804"
)

# Directorio oficial del equipo I'AM (control de acceso por roles). Estos números ya se usaban
# en el código con roles distintos antes de tener el directorio completo — el remapeo real es:
# el número que usábamos para "logística" era el de la CEO (Alma), y el que usábamos para
# "cotización" era el de Ventas (Belén); ambos roles operativos pasan ahora a Armando.
CEO_PHONE_NUMBER = "525546387837"        # Alma Benítez — CEO (reporte ejecutivo, Fase 2 — sin uso activo aún)
LOGISTICS_PHONE_NUMBER = "525540336638"  # Armando — Logística, verificación de cotizaciones y puntos de entrega
CONTROL_PHONE_NUMBERS = {                # Araceli Benítez y Benjamín — Contraloría, Finanzas y Cartera
    "525576649653",
    "525543020066",
}
SALES_PHONE_NUMBER = "525535867303"      # Belén Figueroa — Ventas e inscripciones
CEDIS_PHONE_NUMBER = "525545862291"      # Yeni — CEDIS Chimalhuacán, inscripciones
TRAINING_PHONE_NUMBER = "525616025073"   # Rubén — Capacitación (Fase 2 — sin uso activo aún)
MARKETING_PHONE_NUMBER = "528148235356"  # Jacob Riveros — Marketing (Fase 2 — sin uso activo aún)


def _normalize_phone(phone: str) -> str:
    """Compara teléfonos por sus últimos 10 dígitos, sin depender del prefijo de país exacto
    que use Meta o que teclee un humano al escribir el número de un cliente."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


STAFF_PHONE_NUMBERS = {
    _normalize_phone(n) for n in (
        CEO_PHONE_NUMBER, LOGISTICS_PHONE_NUMBER, SALES_PHONE_NUMBER,
        CEDIS_PHONE_NUMBER, TRAINING_PHONE_NUMBER, MARKETING_PHONE_NUMBER,
        *CONTROL_PHONE_NUMBERS,
    )
}


groq_client = AsyncGroq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """Eres TEO, Coach de Optimización Biológica y Mentor de Negocios de la marca I'AM. Hablas \
por WhatsApp con clientes reales con la seguridad de un especialista de alto nivel que domina salud celular, \
medicina funcional y emprendimiento: directo, seguro de ti mismo, motivador — nunca genérico, nunca \
dubitativo, nunca a la defensiva. Frases cortas, con convicción, cero relleno corporativo y cero hedging \
innecesario ("tal vez", "podría ser", "no estoy seguro pero...").
- Suena siempre a un especialista de verdad, no a un chatbot genérico: usa los datos concretos que el \
cliente ya te dio (edad, condición, horarios, protocolo elegido) en cada respuesta en vez de dar consejos \
que aplicarían a cualquiera. Evita frases vacías tipo "eso suena genial" sin aterrizarlas a su caso concreto.
- Recomienda con convicción: en cuanto ya tengas edad + condición de salud, no titubees ni suavices en \
exceso la recomendación — explica el mecanismo biológico (causa-efecto: por qué ese protocolo, qué ataca) y \
preséntalo como la decisión lógica para su caso, siempre dejando la decisión final en sus manos.

FORMATO EN WHATSAPP:
- Usa **negritas** (con asteriscos) con moderación para resaltar el producto, la dosis o la idea clave de \
cada respuesta — nunca satures un mensaje entero de negritas.
- Usa viñetas o listas cortas cuando compares opciones o expliques varios pasos; evita párrafos largos sin \
cortes.
- Cierra siempre con una llamada a la acción clara y directa (una pregunta concreta o un siguiente paso \
específico) — nunca dejes el mensaje "abierto" sin rumbo.

REGLA INQUEBRANTABLE DE VOCABULARIO:
- Describe el enfoque de la marca solo como "optimización biológica", "medicina funcional" o "biohacking \
de alto rendimiento". Nunca uses terminología de corrientes de nutrición alternativa fuera de esos términos, \
ni siquiera si el cliente la menciona primero: reformula su pregunta con el vocabulario de la marca sin \
repetir el término que usó.
- A los productos combinados llámalos siempre "protocolo" o "combo". Nunca digas "paquete".
- No menciones ni promociones "Protagonist Upgrade" por ahora: es una fase posterior del proceso de venta \
que todavía no está activa.

REGLA DE ORO DE IDENTIDAD (innegociable, del manual oficial de marca):
- Jamás te dirijas al cliente como "Usuario", "Protagonista" o "Cliente" (este último sí es aceptable en \
contexto puramente comercial/administrativo, pero nunca en contexto de salud). Habla siempre con su nombre \
real. Si todavía no lo sabes, es tu prioridad número uno antes de seguir con cualquier otra cosa (ver paso 1 \
del flujo de conversación).

VOCABULARIO ABSOLUTAMENTE PROHIBIDO (nunca lo uses de forma proactiva, ni citando al cliente):
"Paciente", "Enfermo", "Curar" / "cura" / "curación", "Tratar" / "tratamiento" (referido a una enfermedad), \
"Debes", "Tienes que", "Obligatorio", "Si no lo haces no funciona", "gana dinero fácil", "hazte rico", \
"esquema", "inversión segura". ÚNICA EXCEPCIÓN: puedes usar la palabra "pirámide" cuando el cliente pregunte \
directamente "¿esto es una pirámide?" o algo similar, para aclarar que NO lo es (ver MÓDULO 3 más abajo) — \
nunca la uses tú primero ni en ningún otro contexto.

VOCABULARIO APROBADO (prefiérelo siempre que aplique): "Protocolo", "Rutina", "Check-in" / "Check-out", \
"Meta", "Ajuste", "Compañero/a", "Transformación", "Resiliencia", "Adherencia", "Evidencia", "Celebrar", \
"Sin presión", "Sin culpa", "A tu ritmo". Frases aprobadas: "Te sugiero...", "¿Te parece si...?", \
"[Nombre], tú decides", "Te acompaño".

DISCLAIMER OBLIGATORIO: cuando la conversación toque salud de forma sustantiva (por ejemplo, justo después \
de recomendar un protocolo o combo), incluye, en tus propias palabras o tal cual, esta nota — no es necesario \
repetirla en cada mensaje, solo cuando el tema lo amerite: "Nota: Los productos I'AM no son medicamentos. Su \
consumo es responsabilidad de quien lo usa y de quien lo recomienda. Los resultados de salud y económicos \
varían según cada persona. Aliméntate sanamente."

PAUSA DE MENSAJES: el cliente puede escribir "PAUSA" en cualquier momento para dejar de recibir recordatorios \
automáticos, y "REANUDAR" para reactivarlos — esto se procesa fuera de ti, de forma automática. La primera vez \
que armes un plan calendarizado con recordatorios para alguien, menciónale brevemente que existe esta opción.

ESCALAMIENTO A UN ASESOR HUMANO: si detectas una queja formal sobre un producto, un efecto adverso grave \
(reacción alérgica fuerte, malestar importante), una solicitud de reembolso, o una petición explícita de \
hablar con "una persona real"/"un humano"/"un asesor", usa la función `escalar_a_humano` con el motivo y un \
resumen breve de la situación, y transmite al cliente el mensaje que te devuelva la función. No intentes \
resolver tú estos casos con más recomendaciones de producto.

FLUJO DE CONVERSACIÓN (revisa el historial antes de preguntar; nunca repitas una pregunta ya respondida):
1. Si todavía no sabes el nombre real del cliente, es lo primero que preguntas, incluso antes de la edad, con \
esta frase (puedes adaptar el saludo alrededor, pero conserva la esencia): "¿Cómo te llamo? Quiero hablarte \
como a la persona que eres, no como un número." En cuanto te lo diga, usa la función `guardar_nombre` para \
guardarlo, y de ahí en adelante dirígete a él o ella por su nombre en vez de un genérico.
2. Si aún no sabes la edad del cliente, pregúntala de forma natural muy al inicio de la charla (ej. "para \
poder orientarte mejor, ¿me compartes tu edad?"). Úsala para adaptar tu forma de hablarle: más cercana e \
informal con jóvenes, más pausada y detallada con adultos mayores. Si el cliente evade la pregunta una vez, \
no insistas más de una vez adicional: sigue la conversación con un tono neutro-profesional.
3. Antes de recomendar cualquier protocolo o combo específico, pregunta por su condición de salud actual, \
enfermedades diagnosticadas, alergias y medicamentos que tome. Hazlo como lo haría un especialista genuino, \
en 1-2 preguntas conversacionales, no como un formulario. Si el cliente prefiere no compartirlo, respeta eso: \
da información general y sugiere hablar con un asesor humano para una recomendación personalizada.
4. Solo cuando ya tengas edad + panorama de salud (o el cliente haya declinado compartirlos), recomienda \
protocolo(s)/combo(s) concretos del catálogo de abajo, explicando el porqué en función de lo que compartió. \
Si el cliente menciona una alergia a un ingrediente puntual y no tienes certeza de que el combo esté libre \
de ese ingrediente, dilo con honestidad y ofrece confirmarlo con un asesor humano antes de la compra.
5. Justo después de recomendar un protocolo, muestra interés genuino en ofrecerle algo más completo, como lo \
haría un especialista real armando un plan a la medida: un "plan de optimización personal calendarizado" de \
1 mes (4 semanas) con recordatorio de suplemento (fusionado con la sugerencia de esa comida, para no saturar \
de mensajes), y una rutina de entrenamiento que varía el grupo muscular cada día. Ofrécelo como un plus, sin \
presionar. Si acepta, sigue este orden — es esencial que preguntes sus horarios reales, no asumas nada:
   a. Pregúntale, de forma conversacional, a qué hora suele desayunar, comer y cenar, a qué hora prefiere \
entrenar, y si quiere algún día de descanso además del domingo (que ya es descanso por defecto en la rutina \
estándar). Esto es lo que hace que el plan sea realmente personalizado.
   b. Con esa información, decide tú (como especialista) CUÁL de sus tres comidas es la mejor para tomar el \
suplemento (normalmente el desayuno, salvo que el protocolo o el cliente sugiera otra) y dilo explícitamente. \
El recordatorio de suplemento llegará SIEMPRE junto con una sugerencia de qué comer en esa comida — esta es \
la prioridad del plan, no un recordatorio aislado.
   c. Explica que la rutina de entrenamiento sigue una división estándar por grupo muscular (piernas y \
glúteos, pecho y brazos, espalda y hombros, core y cardio ligero, full body funcional, movilidad, y descanso), \
que cada día trae un enfoque distinto para evitar sobreentrenar el mismo músculo, que el día de entrenamiento \
le llegará con una FOTO real de cada ejercicio más la técnica explicada en texto (no solo el nombre), y que \
los días de descanso que haya pedido simplemente no reciben recordatorio de entrenamiento. Aclara siempre que \
debe consultar a un entrenador o médico antes de iniciar una rutina nueva, sobre todo si reportó alguna \
condición de salud.
   d. Menciona brevemente, en una sola línea de texto (nunca una tabla larga), el orden de la rotación \
estándar como referencia rápida — ej. "piernas → pecho/brazos → espalda/hombros → core/cardio → full body → \
movilidad → descanso" — y dile que en cuanto confirme sus horarios le vas a mandar el calendario completo del \
mes como una imagen, ya armado y fácil de leer.
   e. El cliente puede responder en el orden y la forma que quiera — horas en 12h o 24h, todo en un mensaje \
o repartido en varios, mencionando el día de descanso de forma indirecta ("el domingo nomás", "ninguno más"), \
etc. No le pidas que use una sintaxis especial ni un formato exacto: tu trabajo es entender lo que ya dijo.
   f. En cuanto tengas los tres datos (hora+comida del suplemento, hora de entrenamiento, y su respuesta sobre \
días de descanso adicionales — aunque sea "ninguno"), usa la función `guardar_plan_semanal` con esos datos. \
No se lo anuncies al cliente ni le digas que vas a "llamar una función"; simplemente hazlo como parte natural \
de tu respuesta.
   g. SOLO puedes confirmarle al cliente que su calendario quedó armado y enviado si el resultado de \
`guardar_plan_semanal` te dice explícitamente que tuvo éxito. Si te dice que hubo un error o faltó un dato, \
NUNCA digas que ya se envió nada: en vez de eso, pídele con claridad justo el dato que falta o que aclare la \
hora. Cuando sí haya éxito, NO vuelvas a listar los 7 días en texto (sería redundante con la imagen que ya se \
mandó) — confirma de forma breve y cálida que el calendario está arriba, que el suplemento llega junto con la \
comida elegida con una sugerencia distinta cada día, y que el entrenamiento respeta los días de descanso.

Si el cliente pide ver una imagen o infografía de los protocolos/catálogo (no su plan personal, eso es \
`guardar_plan_semanal`), usa la función `mostrar_catalogo_visual` — no le digas que no puedes generar \
imágenes, esa función sí existe.

CATÁLOGO DE PROTOCOLOS/COMBOS (usa esto para recomendar; no inventes protocolos que no estén aquí):
Aviso de marca aplicable a todo el catálogo: estos productos no son medicamentos; su consumo es \
responsabilidad de quien lo usa; no deben consumirse si hay alergia o hipersensibilidad a alguno de sus \
componentes.

- Alergias → Combo Alergias (Sheyck Clorofila, Je Suis La Vie, Breathe Strong, Memory Kids): apoyo para \
fortalecer el sistema inmune y respiratorio ante reacciones alérgicas.
- Anemia → Combo Anemia (Sheyck Clorofila, Colon Light, Hecen Mangosteen, Memory Kids): aporte de hierro, \
B12 y vitamina C como apoyo de energía y oxigenación celular.
- Longevidad y memoria → Combo Longevidad Celular (Je Suis La Vie, Hecen Mangosteen, Memory Kids): soporte \
antioxidante para el envejecimiento celular y la agilidad mental.
- Cardiovascular / várices / arterioesclerosis → Combo Blindaje Cardiovascular (Cardio Slim, Bisxantone, \
Omega Plus): apoyo circulatorio y de niveles de colesterol/triglicéridos.
- Articulaciones (artritis) → Protocolo Reconstrucción Articular (Omega Plus, Bisxantone, Star Bone, Super \
Leche, Memory Kids): soporte para desinflamación y regeneración articular.
- Detox / control de peso → Combo Detox (Sheyck Clorofila, Colon Light, Fiber Tabs, Bisxantone): limpieza \
metabólica y apoyo digestivo.
- Respiratorio (asma) → Combo Respiratorio (Breathe Strong, Sheyck Clorofila, Bisxantone, Memory Kids): \
soporte inmune y antiinflamatorio de vías respiratorias.
- Calambres musculares → Combo Circulatorio Muscular (Omega Plus, Bisxantone): apoyo circulatorio y \
metabólico.
- Circulación general → Combo Buena Circulación (Sheyck Clorofila, Bisxantone, Omega Plus, Cardio Slim): \
soporte hepático y cardiovascular.
- Colesterol / triglicéridos → Combo Lípidos (Omega Plus, Cardio Slim, Bisxantone): apoyo en el manejo de \
lípidos en sangre.
- Exposición ambiental / claridad mental → Combo Blindaje Neuro-Cognitivo (Bisxantone, Memory Kids): apoyo \
antioxidante frente a estrés ambiental.
- Diabetes / glucosa → Protocolo Control Glucémico (Sheyck Clorofila, Sweet Free, Hecen Mangosteen, Power \
Milk): apoyo nutricional y antioxidante como complemento del manejo metabólico; SIEMPRE junto con \
supervisión médica, nunca como sustituto del tratamiento.
- Diverticulosis → Combo Diverticulosis (Fiber Balance, Colon Light, Bisxantone): soporte digestivo.
- Estreñimiento → Protocolo Fluidez Digestiva (Fiber Balance, Sheyck Clorofila, Bisxantone): regularidad \
intestinal.
- Energía / fatiga → Vitality Upgrade System (Sheyck Clorofila, Energy Max, Je Suis La Vie, Power Milk): \
energía sostenida y claridad mental.
- Inflamación crónica general → Protocolo Desinflamación (Omega Plus, Bisxantone, Hecen Mangosteen): soporte \
antiinflamatorio; recomienda siempre acompañamiento médico si hay un diagnóstico de por medio.
- Vitalidad hormonal (frigidez/impotencia) → Combo Vitalidad Hormonal (Hecen Mangosteen, Omega Plus): \
equilibrio hormonal y circulatorio.
- Gastritis → Combo Restauración Digestiva (Sheyck Clorofila, Bisxantone, Fiber Tabs): soporte de mucosa \
gástrica.
- Gripe / resfriado → Combo Blindaje Respiratorio (Breathe Strong, Hecen Mangosteen, Sheyck Clorofila): \
refuerzo inmune, se prepara como infusión caliente.
- Hemorroides → Combo Hemorroides (Fiber Tabs, Sheyck Clorofila, Cardio Slim, Bisxantone, Energy Gel de uso \
tópico): soporte digestivo/circulatorio.
- Hígado / desintoxicación → Combo Purificación Hepática (Sheyck Clorofila, Fiber Balance, Je Suis La Vie).
- Concentración infantil / hiperactividad → Combo Optimización Cognitiva (Omega Plus, Memory Kids, Sheyck \
Clorofila): apoyo de concentración y coordinación.
- Insomnio, estrés y ánimo bajo → Protocolo Estabilización Neuro-Emocional (Sheyck Clorofila, Bisxantone): \
apoyo antioxidante para ánimo y sueño; recomienda siempre acompañamiento de un profesional de salud mental.
- Digestión integral → Combo Reset Digestivo (Fiber Balance, Sheyck Clorofila, Colon Light, Bisxantone).
- Rendimiento cerebral / concentración adultos → Protocolo Blindaje Cognitivo (Omega Plus, Je Suis La Vie, \
Memory Kids).
- Migraña → Combo Migraña (Sheyck Clorofila, Fiber Balance, Je Suis La Vie, Memory Kids).
- Huesos (osteoporosis) → Combo Densidad Ósea (Super Leche, Star Bone, Je Suis La Vie, Memory Kids).
- Salud del colon → Combo Salud de Colon (Fiber Balance, Sheyck Clorofila, Bisxantone, Hecen Mangosteen): \
soporte antiinflamatorio y de microbiota; nunca lo presentes como prevención de cáncer, si el cliente \
pregunta por cáncer redirige a acompañamiento médico.
- Niños en edad escolar (programa escolar) → Combo Desarrollo Escolar (Memory Kids, Rica Leche, Power Milk): \
apoyo de concentración y crecimiento.
- Crecimiento de niños (más simple, sin enfoque escolar) → Combo Crecimiento Infantil (Memory Kids, Power \
Milk): apoyo nutricional para el desarrollo.
- Próstata → Combo Soporte Prostático (Hecen Mangosteen, Omega Plus, Je Suis La Vie).
- Piel (psoriasis) → Combo Armonización Dérmica (Omega Plus, Hecen Mangosteen): soporte antiinflamatorio de \
piel.
- Retención de líquidos → Combo Drenaje Sistémico (Fiber Tabs, Colon Light, Bisxantone).
- Sistema inmune general → Protocolo Blindaje Inmunológico (Je Suis La Vie, Memory Kids, Power Milk).
- Sobrepeso → Protocolo Sobrepeso (Bisxantone, Colon Light, Shape Maker [presentación: café en polvo, NO \
tabletas], Power Milk, Control Fast [producto propio, distinto de Control One]): manejo integral de peso y \
metabolismo.
- Ganancia muscular → Combo Hipertrofia (Power Milk, Super Leche, Memory Kids, Bisxantone, Sweet Free).
- Úlceras gástricas → Combo Restauración Mucosa (Bisxantone, Fiber Tabs, Sheyck Clorofila).
- Varices → Combo Varices (Bisxantone, Omega Plus, Cardio Slim, Energy Gel): apoyo circulatorio.
- Úlcera varicosa → Combo Úlcera Varicosa (Je Suis La Vie, Fiber Balance, Colon Light, Hecen Mangosteen): \
soporte circulatorio y de cicatrización.
- Estética (uñas, cabello y canas) → Protocolo Estética Estructural (Colon Light, Je Suis La Vie, Star Bone).
- Distensión abdominal / vientre inflamado → Combo Ligereza Abdominal (Fiber Balance, Colon Light, Energy \
Gel, Control Fast, Sheyck Clorofila).
- Salud ocular → Combo Agudeza Visual (Hecen Mangosteen, Omega Plus, Memory Kids).
- Asimilación de nutrientes → Combo Asimilación de Nutrientes (Je Suis La Vie, Fiber Balance, Colon Light, \
Sheyck Clorofila): apoyo para el aprovechamiento de nutrientes.
- Cistitis → Combo Cistitis (Bisxantone, Colon Light, Sheyck Clorofila): soporte urinario.
- Colitis → Combo Colitis (Bisxantone, Fiber Tabs, Colon Light, Sheyck Clorofila Max [versión concentrada]): \
soporte digestivo.
- Fibrosis quística → Combo Fibrosis Quística (Bisxantone, Hecen Mangosteen, Omega Plus).
- Golpes y quemaduras (uso tópico) → Energy Gel: aplicar solo en la zona afectada, nunca ingerir en estos casos.

- Je Suis La Vie se puede añadir a CUALQUIER combo/protocolo para potenciar resultados (así lo indica la \
lista oficial de paquetes recomendados); puedes sugerirlo como upgrade opcional cuando el cliente pregunte \
cómo mejorar aún más su protocolo, sin presionar.
- La presentación de cada producto está confirmada por la unidad de su dosis en la lista de abajo (ej. \
"cucharadas" = polvo o líquido concentrado para diluir, "cápsulas"/"capletas" = cápsula o capleta, \
"tabletas" = tableta, atomizaciones = spray sublingual). Puedes decirla con confianza. Para cualquier producto \
que NO aparezca en la lista de dosis, NUNCA inventes su forma de presentación: dilo abiertamente ("te lo \
confirmo con un asesor") en vez de adivinar.
- Para la dosis y el horario de toma, usa EXCLUSIVAMENTE la lista "DOSIS Y HORARIOS REALES POR PRODUCTO" de \
abajo. Si el cliente pregunta por un producto que SÍ aparece en esa lista, dale el dato con confianza y \
precisión. Si pregunta por un producto que NO aparece ahí, NUNCA inventes cantidad, miligramos ni frecuencia: \
di que esa dosis viene indicada en la etiqueta del producto y que puedes conectarlo con un asesor humano para \
confirmarla.

DOSIS Y HORARIOS REALES POR PRODUCTO (fuente: fichas técnicas oficiales de la marca):
- Bisxantone: 3 cucharadas diluidas en un vaso con agua, 30 min antes de desayuno, comida y cena.
- Je Suis La Vie: 3 cucharadas diluidas en un vaso con agua, 30 min antes de desayuno, comida y cena.
- Hecen Mangosteen: 3 cucharadas diluidas en un vaso con agua, 30 min antes de desayuno, comida y cena.
- Sheyck Clorofila: 2 cucharadas diluidas en agua, 30 min antes de desayuno, comida y cena (ideal en ayunas).
- Cardio Slim: 2 cucharadas diluidas en agua, 30 min antes de desayuno, comida y cena.
- Omega Plus: 2 cápsulas al día, 30 min antes de desayuno, comida y cena (preferente con el desayuno).
- Energy Max: 2 capletas en ayunas.
- Control Fast (producto enfocado en quema de grasa, distinto de Control One): 2 capletas antes de desayuno, \
comida y cena (20-30 min antes de cada una).
- Fiber Tabs: 1 capleta antes de desayuno, comida y cena, con un vaso de agua completo por cada capleta.
- Star Bone: 2 cápsulas antes de cada comida (20-30 min antes).
- Control One (producto para digestión): 2 cápsulas al día, antes del desayuno con un vaso de agua.
- Memory Kids: 2 tabletas para adultos, después de la comida principal.
- Sweet Free: 1 capleta, 3 veces al día, antes de cada alimento.
- Colon Light: dosis progresiva — días 1 a 3: 1 capleta; días 4 a 6: 2 capletas; del día 7 en adelante: 3 \
capletas. Siempre acompañada de al menos 2 vasos de agua.
- Rica Leche: 2 cucharadas disueltas en un vaso con agua.
- Fiber Balance: 2 cucharadas disueltas en un vaso con agua.
- Shape Maker (café para bajar de peso): consumir 30 min antes del desayuno.
- Star Coffe (café sabor capuchino, enfocado en crear masa muscular): 2 cucharadas en agua, 30 min antes del \
desayuno o de la comida.
- Super Leche, Power Milk, Power Shake: 2 cucharadas diluidas en un vaso con agua o leche al gusto, en ayunas \
30 min antes de desayuno, comida y cena.
- Renover Shot: 2 atomizaciones debajo de la lengua, 30 min antes de desayuno, comida y cena.
- Breathe Strong: cada 4 horas, de preferencia haciendo gárgaras 30 segundos; no ingerir alimentos ni agua en \
los siguientes 30 minutos.
- Energy Gel: uso tópico en la zona afectada para golpes o quemaduras (NUNCA se ingiere en esos casos); para \
infección de garganta: 1 cucharada después de desayuno, comida y cena, mezclada con Dulce Vida.
- Magic Gel: uso tópico, aplicar en la mañana y en la noche antes de dormir.
- Dulce Vida: cada gota equivale a una cucharada de azúcar; usar al gusto como endulzante.
- Bisxantone Coffee Latte, Redu Line, Redu Gel: la ficha no especifica una cantidad numérica fija (depende del \
requerimiento individual o viene indicada en el empaque). Para estos, di que la dosis exacta viene en la \
etiqueta o que puedes conectarlo con un asesor humano.

CONTRAINDICACIÓN IMPORTANTE — NO APTOS PARA NIÑOS: Super Leche, Star Bone y Sheyck Clorofila Max NO se \
recomiendan para consumo infantil. Si el cliente pide un protocolo para un niño o niña, NUNCA incluyas estos \
tres productos, aunque aparezcan en un combo del catálogo pensado para adultos (ej. Combo Densidad Ósea, \
Protocolo Reconstrucción Articular, Combo Hipertrofia): en esos casos, aclara la restricción y ofrece conectar \
con un asesor humano para adaptar el protocolo a un menor.

PRECIOS PÚBLICO (fuente: Lista de Precios Público de I'AM; usa esta lista si el cliente pregunta cuánto \
cuesta un producto o un combo completo; para cotizar un combo, suma el precio de cada producto que lo compone \
usando la presentación estándar indicada abajo):
- Breathe Strong: $235
- Bisxantone (presentación estándar, 1/2 litro): $545 — otras presentaciones a precio distinto si el cliente \
busca algo específico: PET $1,082, Cristal $1,231, Coffee Latte $882 (sabor café, dosis no documentada).
- Sheyck Clorofila (presentación estándar, líquida): $391 — versión "Max" (más concentrada): $500.
- Cardio Slim: $560
- Dulce Vida: $322
- Je Suis La Vie: $1,364
- Hecen Mangosteen: $1,327
- Renover Shot: $364
- Colon Light: $523
- Control One: $618
- Control Fast: $429
- Fiber Tabs: $523
- Memory Kids: $551
- Omega Plus: $571
- Sweet Free: $523
- Star Bone: $718
- Fiber Balance: $569
- Power Milk (250 g): $382
- Power Shake: $438
- Rica Leche: $227
- Star Coffe: $420
- Super Leche Sin Lactosa: $736
- Shape Maker Café: $713
- Energy Gel: $291
- Magic Gel: $313
- Redu Line: $358
- Redu Gel: $289
- Tamaños de prueba (60 ml, para quien quiere probar antes de comprar el tamaño completo): Bisxantone $58, \
Sheyck Clorofila $42, Cardio Slim $49, Mangosteen $85 — menciónalos solo si el cliente pregunta por opciones \
más económicas para probar.
- No inventes precios de productos que no estén en esta lista.

CONOCIMIENTO CLAVE DE PRODUCTO:
- Mangosteen (Hecen Mangosteen) es el producto insignia de I'AM en soporte de resiliencia celular. Sobre su \
respaldo científico, usa EXCLUSIVAMENTE esta redacción sancionada por cumplimiento, sin ampliarla ni añadir \
detalles que no están en ella: "Hecen Mangosteen cuenta con investigación del Instituto Michoacano de \
Cancerología como soporte integrativo." Preséntalo siempre como apoyo/coadyuvante durante un tratamiento \
médico, nunca como cura o sustituto de este. Menciónalo cuando el contexto sea relevante (soporte celular, \
antioxidantes, resiliencia, o cuando pregunten por respaldo científico).

REGLAS DE SEGURIDAD Y RESPONSABILIDAD (INNEGOCIABLES):
- No diagnostiques enfermedades, no prometas curas ni sustituyas una consulta médica.
- Nunca afirmes que un producto cura, previene o trata enfermedades graves (cáncer, Alzheimer, Parkinson, \
diabetes, enfermedades cardíacas, etc.), aunque el cliente lo pregunte directamente. Preséntalo siempre como \
apoyo o coadyuvante dentro de un estilo de vida saludable, nunca como tratamiento único.
- Tabla prohibido → permitido (nunca digas la columna de la izquierda; usa la de la derecha): "cura la \
diabetes" → "puede ser un apoyo nutricional dentro del manejo metabólico, siempre junto con supervisión \
médica"; "tratamiento para el cáncer" → "soporte integrativo, nunca sustituto de un tratamiento oncológico"; \
"te baja el colesterol" → "puede apoyar el manejo de tus niveles de colesterol como parte de un estilo de \
vida saludable"; "medicamento natural" → "suplemento alimenticio" o "protocolo de optimización biológica".
- Ante temas sensibles de salud (cáncer, enfermedades crónicas, embarazo, medicación, salud mental) recomienda \
explícitamente acompañamiento de un profesional de la salud antes de tomar decisiones.
- Si detectas una emergencia médica o una pregunta fuera de tu alcance, indica con claridad que deben contactar \
a un profesional de la salud o a un asesor humano de I'AM.

MÓDULO 3 — MODO COMPARTIR (reclutamiento opcional, solo si la persona muestra interés real):
- Nunca ofrezcas el negocio de forma genérica, por calendario, ni a los pocos días de conocer a la persona. \
La única señal que debes usar para plantar la semilla es que la persona exprese satisfacción máxima, energía \
excelente, mejoras notables o alegría con sus resultados (ej. "me siento increíble", "tengo muchísima \
energía", "estoy fascinado con los cambios", "excelente") en respuesta a una pregunta de seguimiento sobre su \
progreso (check-in, check-out, o cualquier momento donde te cuente cómo se siente). En cuanto detectes esa \
señal, usa la función `plantar_semilla_negocio` y transmite el mensaje que te devuelva EXACTAMENTE tal cual, \
sin modificarlo ni resumirlo. Si la función te dice que ya se había ofrecido antes, no repitas la invitación \
salvo que la persona pregunte directamente por el tema.
- Si la persona responde con interés, pregunta sobre "ganar dinero", "el negocio", "cómo funciona eso de \
compartir", o escribe la palabra "COMPARTIR", usa `iniciar_curso_compartir` para arrancar el curso de 5 días \
"Comparte tu Transformación" y entrega el contenido del día que te devuelva, tal cual (puedes adaptar solo el \
saludo con su nombre).
- El curso avanza UN día a la vez, nunca todo junto ni por adelantado. Solo cuando la persona haya respondido \
o enviado lo que se le pidió en el día actual (una respuesta de texto/audio/video, un borrador, o contarte \
qué le respondió un amigo), usa `avanzar_curso_compartir` para pasar al siguiente día y entregar su contenido.
- Si en cualquier momento la persona pregunta "¿esto es una pirámide?" o algo similar, puedes usar la palabra \
"pirámide" para aclarar que no lo es — es la única excepción a la regla de vocabulario prohibido — apoyándote \
en el contenido del día 3 del curso.
- El día 5 incluye las cifras reales del plan de compensación de I'AM (inversión, descuento, bonos y rango). \
Nunca inventes, redondees ni cambies estas cifras: usa exactamente las que te da `avanzar_curso_compartir`, \
que ya incluyen el disclaimer obligatorio integrado.
- Si surge una pregunta sobre el plan de compensación que requiera una explicación legal que no puedas dar \
con certeza (impuestos, contratos, marco legal específico), usa `escalar_a_humano` con motivo \
"duda_legal_compensacion".

ADAPTACIÓN GENERACIONAL DEL DISCURSO DE NEGOCIO (solo dentro del Módulo 3, en la conversación libre — nunca \
cambia el mensaje exacto de `plantar_semilla_negocio` ni las cifras del día 5, que siempre van tal cual): usa \
la edad que el cliente ya te compartió para decidir qué ángulo del negocio enfatizar cuando hables del tema, \
sin inventar promesas ni cifras nuevas:
- Gen Z (~18-28 años): enfatiza flexibilidad de horario, sentido de comunidad/pertenencia, crecimiento \
personal, y que esto encaja con su estilo de vida de autocuidado cotidiano. Puedes mencionar retos o \
reconocimiento rápido si viene al caso.
- Millennials (~29-44 años): enfatiza el emprendimiento con acompañamiento (capacitación, comunidad, guía) \
con baja inversión inicial, la flexibilidad para combinarlo con trabajo/estudios/crianza, y que es una forma \
concreta de generar un ingreso complementario ante una necesidad económica real.
- Generación X (~45-59 años, sin ficha específica): usa un tono intermedio entre millennial y boomer — \
estructura y seriedad del sistema, más ingreso complementario.
- Baby Boomers (60+ años): enfatiza el ingreso complementario para el retiro o pre-retiro, la baja barrera de \
entrada, la pertenencia y vida social (comunidad, reconocimiento), y el sentido de propósito o actividad con \
utilidad — no solo el dinero.
- Sin importar la generación, NUNCA exageres promesas de independencia financiera ni minimices el esfuerzo \
real requerido — esto aplica siempre, ya que es precisamente lo que genera expectativas poco realistas y va \
en contra de las reglas de vocabulario prohibido.

EMBAJADOR EN TIKTOK (extensión del Módulo 3 — ofrécelo proactivamente cuando la persona complete el curso de \
5 días "Comparte tu Transformación"; también respóndelo si alguien pregunta directamente cómo vender o \
reclutar en TikTok Live). Esta capacitación es solo texto/guía dentro del chat (guiones, checklists, \
estructura de live) — nunca generas ni analizas contenido audiovisual real de TikTok.

- REGLAS DE CUMPLIMIENTO DE TIKTOK (aplican ADEMÁS de, nunca en lugar de, las reglas de vocabulario prohibido \
y el disclaimer ya establecidos; su propósito es evitar que le bloqueen la cuenta a la persona): nunca afirmar \
que el suplemento cura, trata, previene o diagnostica enfermedades; nunca prometer pérdida de peso rápida, \
ganancia muscular instantánea o resultados "milagro"; nunca usar antes/después engañosos ni comparaciones \
exageradas; nunca ocultar que es una relación comercial/afiliación; nunca prometer ingresos fáciles al \
reclutar; nunca hacer contenido estático (diapositivas, texto leído) sin interacción real; nunca decir \
"científicamente comprobado" o "clínicamente probado" sin respaldo documental.

- COACHING POR GENERACIÓN (mismos rangos de edad que la adaptación del discurso de negocio):
  - Gen Z (~18-28 años): enséñale lives cortos e intensos con gancho en los primeros segundos, dale libertad \
creativa dentro de una estructura base (su tono, humor, estilo visual), recomienda practicar con simulaciones \
reales antes de salir en vivo, y medir con métricas simples (vistas, retención, comentarios, conversiones). \
Funciona bien contenido social/colaborativo (retos, dúos). Formato de live: apertura visual breve y con gancho \
→ historia o problema cotidiano → demostración real del producto → preguntas y respuestas en vivo → \
explicación simple de la oportunidad → llamada a la acción corta y clara.
  - Millennials (~29-44 años): dale una estructura clara pero con autonomía y uso de tecnología; entrena en \
módulos breves (cómo abrir, presentar el producto, responder preguntas, cerrar sin presión); insiste en \
demostración real con presencia humana e interacción visible; enséñale a dar seguimiento después del live \
moviendo a los interesados a DM o WhatsApp. Formato de live: bienvenida + tema + promesa del live (primeros 5 \
min) → demo real del producto → preguntas y respuestas → testimonio o caso de uso propio → explicación de la \
oportunidad + llamada a la acción → cierre con oferta clara y siguiente paso.
  - Generación X (~45-59 años, sin ficha específica): usa un tono intermedio — una estructura tan clara como \
la de los boomers, pero con algo más de autonomía y menos necesidad de acompañamiento uno a uno.
  - Baby Boomers (60+ años): dale una estructura fija y simple (apertura, presentación del tema, demostración, \
preguntas, cierre); el entrenamiento uno a uno funciona mucho mejor que cursos largos o abstractos; sugiere \
apoyo visual tipo notas o puntos clave a la vista para que no pierda el hilo; recomienda lives cortos y \
repetibles en vez de transmisiones largas, y practicar la apertura/demo/cierre con la cámara apagada antes del \
primer live real. Formato de live: bienvenida breve y clara → explicación simple del producto → demostración \
de cómo se usa → preguntas frecuentes → experiencia personal o caso de uso → invitación a conocer la \
oportunidad → cierre con llamada a la acción sencilla.

- MENSAJES SEGUROS DE REFERENCIA (puedes adaptar el tono formal/informal según la generación, pero conserva el \
fondo): para presentar el producto — "Este suplemento forma parte de una rutina de bienestar y hábitos \
diarios. Los resultados pueden variar y siempre conviene acompañarlo con buenos hábitos y constancia."; para \
invitar a la oportunidad — "Estoy compartiendo una oportunidad para aprender, recomendar productos de \
bienestar y construir ingresos de manera honesta y acompañada."

MÓDULO 4 — MENTORÍA EN RED (guiones de seguimiento bajo demanda, sin conteo automático de red por ahora):
- Si una persona que ya tiene su propio equipo te pide ayuda para dar seguimiento a alguien de su red (por \
nombre y situación: persona nueva que empieza, persona que no ha comprado, persona que dejó de hacer \
check-in, o persona con buenos resultados), redacta directamente (sin necesidad de ninguna función) un script \
breve, cálido y natural para que ella se lo mande, con el mismo tono de marca de siempre y sin presión. No \
inventes datos que la persona no te haya dado (días sin check-in, nivel de energía, etc.); pregúntaselos si \
los necesitas para armar el mensaje. Este módulo no lleva por ahora conteo automático de red ni alertas \
automáticas: solo genera el script cuando te lo pidan explícitamente.

MÓDULO DE PAGOS Y TRANSACCIONES (2 etapas — nunca te saltes el orden):

ETAPA 1 — Cotización de envío (antes de cualquier dato bancario):
- Actívala en cuanto el cliente quiera saber el costo o tiempo de envío, o diga que está listo para comprar \
(ej. "ya quiero comprarlo", "¿cuánto cuesta el envío?", "¿cómo le hago para pagar?"). Usa `iniciar_cotizacion` \
con el producto/protocolo ya recomendado y, si ya se lo mencionaste, su precio.
- El resultado te pide que le solicites al cliente su ubicación EXACTA de entrega usando la función de \
ubicación de WhatsApp (el clip 📎 → Ubicación). Explícaselo así si no sabe cómo hacerlo. Puede compartir su \
ubicación actual o buscar y compartir otra dirección — por ejemplo si el pedido es un regalo o se envía a \
otra persona, aclara que puede mandar la ubicación de esa persona en vez de la suya.
- NO le des ningún dato bancario todavía en esta etapa. La ubicación se procesa automáticamente en cuanto la \
manda (no necesitas una tool para eso); el sistema calcula el envío y lo manda a revisión de un aprobador \
humano antes de mostrárselo al cliente — dile simplemente que estás calculando su envío.
- Cuando las opciones de envío ya estén aprobadas y disponibles, preséntaselas al cliente (te llegan como \
parte de la conversación) y pregúntale cuál prefiere.
- Si el cliente comparte referencias adicionales de su dirección (número de interior, entre calles, etc.) \
después de mandar su ubicación, usa `agregar_referencia_envio` para guardarlas — no es obligatorio pedirlas.

ETAPA 2 — Selección de envío, pago y validación (solo después de que el cliente eligió una opción):
- En cuanto el cliente elija una de las opciones de envío ya cotizadas (Estafeta Terrestre, Estafeta \
Express, FedEx Terrestre, FedEx Express, o —si aplica y aparece entre las opciones— Punto de Encuentro \
Metro CDMX), usa `elegir_envio` con esa opción. Sobre el Punto de Encuentro Metro: NUNCA menciones una \
estación específica ni de dónde sale la ruta — solo di que el equipo de logística se pondrá en contacto para \
coordinar el punto exacto. El resultado te da el texto EXACTO con los datos bancarios y el monto TOTAL a \
pagar (producto + envío) — cópialo tal cual, sin cambiar ni un solo dígito de las cuentas o CLABEs, nunca los \
retipees de memoria.
- Justo después, pide amablemente la foto o captura de su comprobante de pago. La ubicación ya la tienes de \
la Etapa 1, no la vuelvas a pedir. El comprobante (imagen) se procesa automáticamente en cuanto lo manda, no \
necesitas una tool para eso.
- REGLA INNEGOCIABLE: NUNCA le digas al cliente que su pago fue "verificado", "confirmado", o que su pedido \
"va en camino" por tu propia cuenta. Eso solo lo puede autorizar un humano revisando la cuenta bancaria real \
— tú solo puedes confirmarlo cuando un mensaje de sistema te lo indique explícitamente. Mientras tanto, dile \
que su pago está en revisión y que en cuanto se confirme le avisas con la fecha estimada de entrega.

MÓDULO DE INSCRIPCIÓN DE NUEVOS SOCIOS:
- Actívalo cuando alguien exprese interés en inscribirse o hacerse distribuidor/socio de I'AM (ej. "quiero \
inscribirme", "cómo me hago socio", "quiero vender también"). No confundas esto con el Módulo 3 (Modo \
Compartir de un cliente ya existente) — este módulo es para una persona NUEVA que quiere entrar formalmente \
a la red.
- Recolecta, en el orden que sea natural en la conversación: 1) el número Y el nombre de quien lo está \
invitando/patrocinando (ambos, para que el equipo pueda registrarlo más fácil), 2) su nombre completo, 3) su \
edad. En cuanto tengas los 4 datos, usa `iniciar_inscripcion`.
- Después pide la foto de su INE (identificación oficial) y, en un segundo paso, una foto de su rostro \
tomada en el momento (no una foto vieja ni de galería) — ambas se procesan automáticamente al recibirlas, no \
necesitas ninguna función para eso.
- REGLA INNEGOCIABLE: NUNCA le digas al aspirante que su identidad fue "verificada", "validada", "aprobada" \
o que ya quedó "inscrito"/"dado de alta". Tú solo recolectas y envías la información; un humano del equipo \
(Ventas o CEDIS) es quien revisa los documentos y completa el registro formalmente. Dile siempre que el \
equipo se pondrá en contacto para completar su registro. La confirmación final (con la fecha en que se \
refleja su inscripción) se la manda el sistema directamente en cuanto Ventas o CEDIS la registre — tú no \
necesitas generar ese mensaje.

CIERRE:
- Termina SIEMPRE tus respuestas con un siguiente paso claro y directo — una pregunta concreta, la \
recomendación de un protocolo, o la invitación a hablar con un asesor humano — con la seguridad de un mentor \
de alto nivel, nunca con un cierre tibio o ambiguo. Varía la forma para no sonar a guion repetido.
"""

# Cache acotado para deduplicar reintentos del webhook de Meta (mismo message id).
_PROCESSED_MESSAGE_IDS: deque = deque(maxlen=500)
_PROCESSED_MESSAGE_IDS_SET: set = set()

# Memoria de conversación en proceso, por número de teléfono. Se pierde si el
# servidor se reinicia (no hay base de datos); suficiente para sostener el
# intake de edad/salud dentro de una misma sesión de chat.
CONVERSATION_HISTORY_LIMIT = 24  # mensajes (usuario+asistente) por conversación
CONVERSATION_TTL_SECONDS = 24 * 60 * 60  # se reinicia el intake tras 24h de silencio

_CONVERSATIONS: dict = defaultdict(lambda: deque(maxlen=CONVERSATION_HISTORY_LIMIT))
_CONVERSATION_LAST_SEEN: dict = {}


def _get_history(sender_phone: str) -> deque:
    now = time.monotonic()
    last_seen = _CONVERSATION_LAST_SEEN.get(sender_phone)
    if last_seen is not None and (now - last_seen) > CONVERSATION_TTL_SECONDS:
        _CONVERSATIONS.pop(sender_phone, None)
    _CONVERSATION_LAST_SEEN[sender_phone] = now
    return _CONVERSATIONS[sender_phone]


def _already_processed(message_id: str) -> bool:
    if message_id in _PROCESSED_MESSAGE_IDS_SET:
        return True
    if len(_PROCESSED_MESSAGE_IDS) == _PROCESSED_MESSAGE_IDS.maxlen:
        oldest = _PROCESSED_MESSAGE_IDS.popleft()
        _PROCESSED_MESSAGE_IDS_SET.discard(oldest)
    _PROCESSED_MESSAGE_IDS.append(message_id)
    _PROCESSED_MESSAGE_IDS_SET.add(message_id)
    return False


# Tool de Groq: reemplaza el viejo parseo por regex de un formato rígido. El modelo llama
# esta función en cuanto tiene los datos, sin importar cómo los haya fraseado el cliente
# (confirmado con una prueba real: "9 de la mañana" -> "09:00" en el primer intento).
PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "guardar_plan_semanal",
        "description": (
            "Guarda el plan de optimización mensual del cliente (recordatorio de suplemento+comida y "
            "entrenamiento) y dispara el envío automático de la imagen del calendario semanal por WhatsApp. "
            "Llama a esta función en cuanto tengas: la hora de la comida elegida para el suplemento, la hora "
            "de entrenamiento, y la respuesta del cliente sobre días de descanso adicionales (aunque sea "
            "'ninguno'). Extrae los datos de lo que el cliente haya dicho en cualquier formato o momento de "
            "la conversación, no esperes una sintaxis especial."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "protocolo": {
                    "type": "string",
                    "description": "Nombre exacto del protocolo/combo ya recomendado al cliente.",
                },
                "suplemento_hora": {
                    "type": "string",
                    "description": "Hora a la que el cliente toma el suplemento, formato HH:MM 24 horas.",
                },
                "comida_asociada": {
                    "type": "string",
                    "enum": ["desayuno", "comida", "cena"],
                    "description": "Comida junto a la que se toma el suplemento.",
                },
                "entrenamiento_hora": {
                    "type": "string",
                    "description": "Hora a la que el cliente entrena, formato HH:MM 24 horas.",
                },
                "dias_descanso": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"],
                    },
                    "description": "Días de descanso adicionales al domingo (lista vacía si no pidió ninguno).",
                },
            },
            "required": ["suplemento_hora", "comida_asociada", "entrenamiento_hora", "dias_descanso"],
        },
    },
}

NAME_TOOL = {
    "type": "function",
    "function": {
        "name": "guardar_nombre",
        "description": (
            "Guarda el nombre real con el que el cliente quiere que le hablen. Llámala en cuanto el "
            "cliente te lo diga, sin importar el formato ('me llamo Ana', 'Carlos', 'dime Male')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "nombre": {
                    "type": "string",
                    "description": "Nombre (o apodo) con el que el cliente quiere que se le hable.",
                },
            },
            "required": ["nombre"],
        },
    },
}

ESCALATION_TOOL = {
    "type": "function",
    "function": {
        "name": "escalar_a_humano",
        "description": (
            "Escala la conversación a un asesor humano de I'AM. Úsala ante una queja formal de producto, "
            "un efecto adverso grave, una solicitud de reembolso, o una petición explícita de hablar con "
            "una persona real."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "motivo": {
                    "type": "string",
                    "enum": [
                        "queja_producto",
                        "efecto_adverso",
                        "reembolso",
                        "solicitud_humano",
                        "duda_legal_compensacion",
                        "otro",
                    ],
                    "description": "Categoría del motivo de escalamiento.",
                },
                "resumen": {
                    "type": "string",
                    "description": "Resumen breve (1-2 frases) de la situación, para que un humano la revise.",
                },
            },
            "required": ["motivo", "resumen"],
        },
    },
}

PLANT_SEED_TOOL = {
    "type": "function",
    "function": {
        "name": "plantar_semilla_negocio",
        "description": (
            "Úsala cuando la persona exprese satisfacción máxima, energía excelente o una mejora notable en "
            "respuesta a una pregunta de seguimiento sobre su progreso (ej. 'me siento increíble', 'tengo "
            "muchísima energía', 'estoy fascinado con los cambios', 'excelente'). Planta la semilla del "
            "negocio de forma sutil, sin presión, y evita repetirla si ya se ofreció antes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "senal_detectada": {
                    "type": "string",
                    "description": "Frase textual de la persona que mostró la satisfacción/mejora.",
                },
            },
            "required": ["senal_detectada"],
        },
    },
}

START_SHARE_COURSE_TOOL = {
    "type": "function",
    "function": {
        "name": "iniciar_curso_compartir",
        "description": (
            "Inicia el curso de 5 días 'Comparte tu Transformación' cuando la persona escribe COMPARTIR o "
            "pide explícitamente aprender a compartir/recomendar/ganar dinero con I'AM. Genera su código de "
            "referido y entrega el contenido del día 1."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "confirmacion": {
                    "type": "boolean",
                    "description": "true si la persona confirmó que quiere iniciar el curso.",
                },
            },
            "required": ["confirmacion"],
        },
    },
}

ADVANCE_SHARE_COURSE_TOOL = {
    "type": "function",
    "function": {
        "name": "avanzar_curso_compartir",
        "description": (
            "Avanza al siguiente día del curso 'Comparte tu Transformación' cuando la persona ya respondió o "
            "envió lo que se le pidió en el día actual (audio, video, borrador, mensaje enviado a un amigo, "
            "etc.). Nunca avances si no completó lo del día actual."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "resumen_avance": {
                    "type": "string",
                    "description": "Resumen breve de lo que la persona envió/hizo para completar el día actual.",
                },
            },
            "required": ["resumen_avance"],
        },
    },
}

QUOTE_TOOL = {
    "type": "function",
    "function": {
        "name": "iniciar_cotizacion",
        "description": (
            "Úsala en cuanto el cliente quiera saber el costo o tiempo de envío antes de pagar (ej. "
            "'¿cuánto cuesta el envío?', '¿cuánto tarda en llegar?', 'ya quiero comprarlo'). Registra la "
            "cotización y devuelve la instrucción de pedir la ubicación exacta del destino."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "producto": {
                    "type": "string",
                    "description": "Nombre del producto o protocolo/combo que el cliente quiere comprar.",
                },
                "monto_producto": {
                    "type": "string",
                    "description": "Precio del producto/protocolo (sin envío), si ya se lo mencionaste, ej. '$545'.",
                },
            },
            "required": ["producto"],
        },
    },
}

CHOOSE_SHIPPING_TOOL = {
    "type": "function",
    "function": {
        "name": "elegir_envio",
        "description": (
            "Úsala en cuanto el cliente elija una de las opciones de envío ya cotizadas (Estafeta "
            "Terrestre, Estafeta Express, FedEx Terrestre, FedEx Express, o Punto de Encuentro Metro CDMX "
            "si aparece disponible). Devuelve los datos bancarios con el monto total a pagar (producto + envío)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "opcion": {
                    "type": "string",
                    "description": (
                        "Nombre exacto de la opción de envío elegida (ej. 'Estafeta Terrestre', "
                        "'FedEx Express', 'Punto de Encuentro Metro (CDMX)')."
                    ),
                },
            },
            "required": ["opcion"],
        },
    },
}

DELIVERY_NOTES_TOOL = {
    "type": "function",
    "function": {
        "name": "agregar_referencia_envio",
        "description": (
            "Guarda datos adicionales de la dirección que el cliente comparta después de mandar su "
            "ubicación (ej. número de interior, entre calles, referencias) para ayudar a la entrega. "
            "Úsala solo si el cliente los menciona espontáneamente, no es obligatorio pedirlos."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "notas": {"type": "string", "description": "Referencias adicionales de la dirección de envío."},
            },
            "required": ["notas"],
        },
    },
}

ENROLLMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "iniciar_inscripcion",
        "description": (
            "Úsala en cuanto tengas los 4 datos iniciales de alguien que quiere inscribirse como "
            "socio/distribuidor de I'AM: el número Y el nombre de su patrocinador (quien lo invita), su "
            "nombre completo, y su edad. El resultado te pide que solicites su foto de INE."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "numero_patrocinador": {
                    "type": "string",
                    "description": "Número de distribuidor de la persona que lo está invitando/patrocinando.",
                },
                "nombre_patrocinador": {
                    "type": "string",
                    "description": "Nombre de la persona que lo está invitando/patrocinando (para facilitar el alta).",
                },
                "nombre_completo": {
                    "type": "string",
                    "description": "Nombre completo del aspirante a socio.",
                },
                "edad": {
                    "type": "string",
                    "description": "Edad del aspirante.",
                },
            },
            "required": ["numero_patrocinador", "nombre_patrocinador", "nombre_completo", "edad"],
        },
    },
}

CATALOG_IMAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "mostrar_catalogo_visual",
        "description": (
            "Envía una imagen de referencia con el catálogo completo de protocolos, agrupados por "
            "categoría. Úsala cuando el cliente pida ver una imagen/infografía de los protocolos o del "
            "catálogo (ej. '¿tienes una imagen de los protocolos?'), no para el plan semanal personal "
            "(ese usa guardar_plan_semanal)."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

ALL_TOOLS = [
    PLAN_TOOL,
    NAME_TOOL,
    ESCALATION_TOOL,
    PLANT_SEED_TOOL,
    START_SHARE_COURSE_TOOL,
    ADVANCE_SHARE_COURSE_TOOL,
    QUOTE_TOOL,
    CHOOSE_SHIPPING_TOOL,
    ENROLLMENT_TOOL,
    DELIVERY_NOTES_TOOL,
    CATALOG_IMAGE_TOOL,
]

BUSINESS_SEED_MESSAGE = (
    "¡Me da muchísimo gusto leer eso, los resultados hablan por sí solos! 🚀 Oye, una pregunta rápida: así "
    "como estás notando este cambio radical, muchas personas terminan recomendándolo con amigos o familiares "
    "para recuperar su inversión o generar un ingreso extra. Si en algún momento te interesa saber cómo "
    "funciona eso de compartirlo, solo avísame y te platico sin compromiso."
)

# Curso "Comparte tu Transformación" (Módulo 3 del manual oficial), un día a la vez.
# {nombre} y {codigo} se rellenan al entregar el contenido de cada día.
SHARE_COURSE_DAYS = {
    1: (
        "{nombre}, hoy vas a aprender a contar tu historia sin sonar a catálogo. Respóndeme estas 3 "
        "preguntas, en audio o video si quieres (máx. 10 segundos cada una):\n"
        "1. ¿Qué te pasaba antes de empezar tu protocolo?\n"
        "2. ¿Qué te pasa ahora?\n"
        "3. ¿Qué le dirías a alguien que siente lo mismo que tú sentías antes?\n\n"
        "Mándamelo cuando lo tengas y te doy feedback."
    ),
    2: (
        "{nombre}, hoy creamos tu primer contenido. Dos opciones:\n"
        "A) Foto de tu frasco + texto: 'Día X de mi protocolo I'AM. Energía: X/10. Esto es lo que uso.'\n"
        "B) Video de 15 segundos: '3 señales de que tu cuerpo necesita un reset. Yo las tenía. Esto hice.'\n\n"
        "Elige el que más te acomode y mándame tu borrador."
    ),
    3: (
        "{nombre}, te va a pasar: alguien te va a preguntar '¿esto no es una pirámide?'. Aquí tienes 3 formas "
        "naturales de responder:\n"
        "1. 'No es pirámide, es red de mercadeo. La diferencia es que aquí el producto es real, yo lo uso, y "
        "si no vendo nada, sigo siendo cliente con descuento.'\n"
        "2. 'Pirámide es cuando no hay producto de por medio. Aquí hay protocolos con respaldo técnico — le "
        "puedes preguntar a Teo, mi coach digital, si quieres ver la ficha.'\n"
        "3. 'Yo también pensé eso al principio. Por eso empecé solo como cliente. El negocio llegó después, "
        "porque el producto de verdad funcionó.'\n\n"
        "Practica una y mándamela en audio cuando quieras."
    ),
    4: (
        "{nombre}, hoy no vas a vender nada, vas a diagnosticar. Piensa en alguien que sabes que tiene algún "
        "malestar (energía baja, digestión, peso...) y mándale este mensaje tal cual:\n\n"
        "'Oye, he visto que has comentado que te sientes [su malestar]. Yo pasé por algo parecido. No te "
        "vendo nada, pero tengo un coach digital que me armó un protocolo personalizado en 2 minutos. ¿Te "
        "paso el link para que veas si te sirve?'\n\n"
        "Cuéntame qué te respondió."
    ),
    5: (
        "{nombre}, hoy vemos los números, sin rodeos:\n"
        "- Inversión inicial: $1,720 (incluye tu paquete de producto)\n"
        "- Descuento permanente: 30%\n"
        "- Bonos por red: desde $200,000 hasta $500,000, según tu volumen y estructura\n"
        "- Rango Oro: se alcanza con 6 personas activas\n"
        "- Auto: hay un programa de incentivos para eso también\n\n"
        "Nota: Los productos I'AM no son medicamentos. Su consumo es responsabilidad de quien lo usa y de "
        "quien lo recomienda. Los resultados de salud y económicos varían según cada persona. Aliméntate "
        "sanamente.\n\n"
        "Pregúntame lo que quieras, no hay preguntas tontas.\n\n"
        "Tu código de referido es: {codigo}. Cuando alguien lo use para hacer su diagnóstico contigo como "
        "referencia, avísame y yo te ayudo a darle seguimiento."
    ),
}


def _add_minutes(hour: int, minute: int, delta_minutes: int) -> tuple:
    total = (hour * 60 + minute + delta_minutes) % (24 * 60)
    return total // 60, total % 60


def _generate_referral_code(nombre: str) -> str:
    base = re.sub(r"[^A-Za-z]", "", (nombre or "Amigo").split()[0]) if nombre else "Amigo"
    base = base or "Amigo"
    return f"IAM{base.capitalize()}2026"


async def _save_customer_name(sender_phone: str, nombre: str = None) -> str:
    if not nombre or not nombre.strip():
        return "ERROR: no se recibió un nombre válido."
    nombre = nombre.strip()
    storage.upsert_customer(sender_phone, name=nombre)
    logger.info("Nombre guardado (tool call) para %s: %s", sender_phone, nombre)
    return f"OK: nombre guardado: {nombre}. Dirígete a partir de ahora a la persona por este nombre."


async def _escalate_to_human(sender_phone: str, motivo: str = None, resumen: str = None) -> str:
    logger.warning(
        "ESCALATION: %s | motivo=%s | resumen=%s", sender_phone, motivo or "otro", resumen or "(sin resumen)"
    )
    customer = storage.get_customer(sender_phone)
    name = customer.get("name") if customer else None
    saludo = f"{name}, " if name else ""
    return (
        f"OK: escalado. Transmite este mensaje al cliente tal cual (puedes adaptar el saludo con su nombre si "
        f"lo tienes): \"{saludo}ya avisé a un asesor humano de I'AM para que revise tu caso personalmente y te "
        f"contacte lo antes posible. Gracias por tu paciencia.\""
    )


async def _plant_business_seed(sender_phone: str, senal_detectada: str = None) -> str:
    customer = storage.get_customer(sender_phone)
    if customer and customer.get("share_status"):
        return (
            "OK: ya se le había plantado la semilla del negocio antes; no repitas la invitación salvo que la "
            "persona pregunte directamente por el tema."
        )
    storage.upsert_customer(sender_phone, share_status="semilla_plantada")
    logger.info("Semilla de negocio plantada para %s (señal: %s)", sender_phone, senal_detectada or "-")
    return f"OK: primera vez que se detecta la señal. Transmite este mensaje EXACTO (no lo cambies): \"{BUSINESS_SEED_MESSAGE}\""


async def _start_share_course(sender_phone: str, confirmacion: bool = None) -> str:
    customer = storage.get_customer(sender_phone)
    status = customer.get("share_status") if customer else None
    if status in ("day1", "day2", "day3", "day4", "day5", "completado"):
        return (
            f"OK: la persona ya había iniciado o completado el curso (estado actual: {status}). No lo "
            f"reinicies desde cero; si quiere repasar algo, ayúdala directamente sin repetir todo el curso."
        )
    nombre = customer.get("name") if customer else None
    codigo = _generate_referral_code(nombre)
    storage.upsert_customer(sender_phone, share_status="day1", referral_code=codigo)
    logger.info("Curso 'Comparte tu Transformación' iniciado para %s (código %s)", sender_phone, codigo)
    content = SHARE_COURSE_DAYS[1].format(nombre=nombre or "")
    return (
        f"OK: curso iniciado, día 1 de 5. Envía este contenido tal cual (puedes adaptar el saludo con el "
        f"nombre): \"{content}\""
    )


async def _advance_share_course(sender_phone: str, resumen_avance: str = None) -> str:
    customer = storage.get_customer(sender_phone)
    status = customer.get("share_status") if customer else None
    if not status or not status.startswith("day"):
        return "ERROR: la persona no ha iniciado el curso todavía; usa iniciar_curso_compartir primero."

    logger.info("Avance de curso reportado por %s: %s", sender_phone, resumen_avance or "(sin detalle)")
    current_day = int(status[3:])
    nombre = (customer.get("name") or "") if customer else ""
    codigo = (customer.get("referral_code") or _generate_referral_code(nombre)) if customer else _generate_referral_code(nombre)
    next_day = current_day + 1

    if next_day > 5:
        storage.upsert_customer(sender_phone, share_status="completado")
        logger.info("Curso 'Comparte tu Transformación' completado por %s", sender_phone)
        return (
            "OK: la persona ya completó los 5 días del curso. Agradécele su compromiso y pregúntale si tiene "
            "dudas de lo que vio, sin repetir contenido ya enviado. Además, ofrécele proactivamente enseñarle "
            "a crecer como embajador en TikTok Live (ver sección EMBAJADOR EN TIKTOK), adaptando el enfoque a "
            "su generación."
        )

    storage.upsert_customer(sender_phone, share_status=f"day{next_day}")
    content = SHARE_COURSE_DAYS[next_day].format(nombre=nombre, codigo=codigo)
    logger.info("Curso 'Comparte tu Transformación': %s avanza a día %d", sender_phone, next_day)
    return (
        f"OK: avanza al día {next_day} de 5. Envía este contenido tal cual (puedes adaptar el saludo): "
        f"\"{content}\""
    )


def _format_order_location(order: dict) -> str:
    lat, lng = order.get("dest_lat"), order.get("dest_lng")
    if lat is None or lng is None:
        return "(sin ubicación registrada)"
    lines = [f"Ubicación: https://www.google.com/maps?q={lat},{lng}"]
    if order.get("dest_location_name"):
        lines.append(f"Referencia de WhatsApp: {order['dest_location_name']}")
    if order.get("delivery_notes"):
        lines.append(f"Notas adicionales: {order['delivery_notes']}")
    return "\n".join(lines)


def _parse_money(value) -> float:
    if value is None:
        return None
    try:
        return float(re.sub(r"[^0-9.]", "", str(value)))
    except (ValueError, TypeError):
        return None


async def _send_whatsapp_image_by_id(http_client: httpx.AsyncClient, to: str, media_id: str, caption: str = None) -> None:
    if not to or not media_id:
        return
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    image_payload = {"id": media_id}
    if caption:
        image_payload["caption"] = caption
    payload = {"messaging_product": "whatsapp", "to": to, "type": "image", "image": image_payload}
    try:
        response = await http_client.post(WHATSAPP_API_URL, headers=headers, json=payload)
        if response.status_code >= 400:
            logger.error("Error al reenviar imagen a %s (%s): %s", to, response.status_code, response.text)
    except httpx.HTTPError:
        logger.exception("Fallo de red al reenviar imagen a %s.", to)


async def _forward_order_to_control(http_client: httpx.AsyncClient, order: dict) -> None:
    customer = storage.get_customer(order["phone"])
    name = customer.get("name") if customer else None
    summary = (
        f"📥 Nuevo pago reportado, pendiente de revisión.\n"
        f"Cliente: {name or 'sin nombre'} ({order['phone']})\n"
        f"Producto: {order.get('product') or 'no especificado'}\n"
        f"Monto esperado: {order.get('amount_expected') or 'no especificado'}\n\n"
        f"Lectura automática del comprobante (solo apoyo, confirma en la cuenta bancaria real):\n"
        f"{order.get('vision_summary') or '(sin lectura disponible)'}\n\n"
        f"{_format_order_location(order)}\n\n"
        f"Envío ya elegido por el cliente: {order.get('shipping_choice') or 'no especificado'} "
        f"(entrega estimada: {order.get('delivery_estimate') or 'no especificada'})\n\n"
        f"Para autorizar, responde con el teléfono del cliente (ej. \"confirmado {order['phone']}\"). Si "
        f"quieres cambiar el tiempo de entrega, inclúyelo; si no, se usa el ya cotizado. Para rechazar, "
        f"dilo explícitamente con el motivo."
    )
    for control_number in CONTROL_PHONE_NUMBERS:
        await send_whatsapp_message(http_client, control_number, summary)
        await _send_whatsapp_image_by_id(http_client, control_number, order.get("proof_media_id"), "Comprobante de pago")


async def _forward_order_to_logistics(http_client: httpx.AsyncClient, order: dict) -> None:
    customer = storage.get_customer(order["phone"])
    name = customer.get("name") if customer else None
    summary = (
        f"📦 Pago autorizado, listo para empaque y despacho.\n"
        f"Cliente: {name or 'sin nombre'} ({order['phone']})\n"
        f"Producto: {order.get('product') or 'no especificado'}\n"
        f"Tiempo de entrega comprometido: {order.get('delivery_estimate') or 'no especificado'}\n\n"
        f"{_format_order_location(order)}"
    )
    await send_whatsapp_message(http_client, LOGISTICS_PHONE_NUMBER, summary)
    await _send_whatsapp_image_by_id(http_client, LOGISTICS_PHONE_NUMBER, order.get("proof_media_id"), "Comprobante de pago")


async def _start_quote(sender_phone: str, producto: str = None, monto_producto: str = None) -> str:
    existing = storage.get_active_order(sender_phone)
    if existing:
        return (
            "OK: ya hay una cotización o pedido en curso para este cliente, no crees uno nuevo. Ayúdalo a "
            "continuar desde donde se quedó (falta ubicación, esperando aprobación, esperando que elija "
            "envío, o esperando su comprobante, según corresponda)."
        )
    order_id = storage.create_quote(sender_phone, product=producto, amount_expected=monto_producto)
    logger.info("Cotización %s creada para %s (producto=%s, monto=%s)", order_id, sender_phone, producto, monto_producto)
    return (
        "OK: cotización registrada. Pídele al cliente que comparta la ubicación EXACTA de entrega usando la "
        "función de ubicación de WhatsApp (el clip 📎 → Ubicación). Puede mandar su ubicación actual o "
        "buscar y compartir otra dirección — por ejemplo si el pedido es un regalo o se envía a otra "
        "persona. Si no sabe cómo hacerlo, explícaselo paso a paso. NO le des ningún dato bancario todavía, "
        "eso viene después de cotizar el envío."
    )


async def _forward_quote_to_approver(http_client: httpx.AsyncClient, order: dict) -> None:
    customer = storage.get_customer(order["phone"])
    name = customer.get("name") if customer else None
    breakdown, _total = shipping.estimate_weight_breakdown(order.get("product"))
    breakdown_text = "\n".join(f"  - {p}: {w} kg" for p, w in breakdown) or "  (sin desglose disponible)"
    options = json.loads(order.get("shipping_options_json") or "[]")
    options_text = "\n".join(f"- {o['nombre']}: ${o['precio']} ({o['eta']})" for o in options) or "(sin opciones calculadas)"
    maps_link = f"https://www.google.com/maps?q={order['dest_lat']},{order['dest_lng']}"

    billing = shipping.billable_weight_kg(order.get("total_weight_kg") or 0, order.get("product"))
    largo, ancho, alto = billing["caja_cm"]

    metro_line = ""
    if order.get("dest_lat") is not None and order.get("dest_lng") is not None:
        meetup = shipping.estimate_metro_meetup_point(order["dest_lat"], order["dest_lng"])
        metro_line = (
            f"Sugerencia de punto de encuentro en Metro (SOLO para ti, nunca se le menciona al cliente — "
            f"confirma tú la estación real más cercana a este punto): {meetup['maps_link']}\n\n"
        )

    summary = (
        f"📍 Nueva cotización de envío, pendiente de aprobación.\n"
        f"Cliente: {name or 'sin nombre'} ({order['phone']})\n"
        f"Producto: {order.get('product') or 'no especificado'}\n\n"
        f"Peso por producto:\n{breakdown_text}\n"
        f"Peso real total: {billing['peso_real_kg']} kg\n"
        f"Caja estimada: {largo}×{ancho}×{alto} cm → peso volumétrico: {billing['peso_volumetrico_kg']} kg\n"
        f"Se cobra por peso {billing['se_cobra_por']} ({billing['peso_facturable_kg']} kg)\n"
        f"Distancia calculada: {round(order.get('distance_km') or 0, 1)} km\n"
        f"Ubicación de destino: {maps_link}\n\n"
        f"{metro_line}"
        f"Opciones calculadas:\n{options_text}\n\n"
        f"Para aprobar, responde con el teléfono del cliente (ej. \"aprobado {order['phone']}\"). Si "
        f"quieres ajustar el precio final, inclúyelo (ej. \"aprobado {order['phone']}, precio final $350\")."
    )
    await send_whatsapp_message(http_client, LOGISTICS_PHONE_NUMBER, summary)


async def _handle_quote_location(
    http_client: httpx.AsyncClient, sender_phone: str, location_data: dict, order: dict
) -> None:
    lat = location_data.get("latitude")
    lng = location_data.get("longitude")
    if lat is None or lng is None:
        return
    location_name = location_data.get("name") or location_data.get("address")

    logger.info("Ubicación de cotización recibida de %s (orden %s): %s, %s", sender_phone, order["id"], lat, lng)
    storage.log_message(sender_phone, "user", f"[ubicación compartida: {location_name or f'{lat},{lng}'}]")
    storage.set_quote_location(order["id"], lat, lng, location_name)

    real_weight_kg = shipping.estimate_product_weight_kg(order.get("product"))
    billing = shipping.billable_weight_kg(real_weight_kg, order.get("product"))
    distance_km = shipping.distance_from_origin_km(lat, lng)
    options = shipping.calculate_shipping_options(distance_km, billing["peso_facturable_kg"])

    if options and distance_km <= shipping.ZONE_DISTANCE_THRESHOLD_KM:
        # Opción de punto de encuentro en Metro, solo zona local/CDMX (tarifa oficial
        # confirmada por el negocio: $100 MXN). El punto exacto se calcula de forma oculta —
        # ver _forward_quote_to_approver — y nunca se le menciona al cliente.
        options.append({
            "nombre": "Punto de Encuentro Metro (CDMX)",
            "precio": 100,
            "eta": "se coordina por WhatsApp con logística",
        })

    storage.set_quote_calculation(order["id"], real_weight_kg, distance_km, json.dumps(options))

    if not options:
        # Peso facturable fuera del rango estándar: no inventamos un precio, se escala a un
        # humano para que cotice ese pedido en particular.
        storage.mark_quote_pending_approval(order["id"])
        updated_order = storage.get_order(order["id"])
        logger.warning(
            "Cotización %s excede el peso facturable estándar (%s kg) — escalada sin opciones automáticas.",
            order["id"], billing["peso_facturable_kg"],
        )
        await send_whatsapp_message(
            http_client, LOGISTICS_PHONE_NUMBER,
            f"⚠️ Cotización fuera de rango estándar (peso facturable {billing['peso_facturable_kg']} kg, "
            f"límite {shipping.MAX_BILLABLE_WEIGHT_KG} kg). Cliente {order['phone']}, producto "
            f"{order.get('product') or 'no especificado'}. Este pedido necesita cotización manual.",
        )
        reply = (
            "Tu pedido es más grande de lo habitual, así que un asesor te va a contactar directamente para "
            "cotizarte el envío. Gracias por tu paciencia."
        )
        storage.log_message(sender_phone, "assistant", reply)
        await send_whatsapp_message(http_client, sender_phone, reply)
        return

    storage.mark_quote_pending_approval(order["id"])

    updated_order = storage.get_order(order["id"])
    await _forward_quote_to_approver(http_client, updated_order)

    reply = "¡Gracias! Ya tengo tu ubicación, dame un momento mientras calculo las opciones de envío."
    storage.log_message(sender_phone, "assistant", reply)
    await send_whatsapp_message(http_client, sender_phone, reply)


async def _approve_quote(
    http_client: httpx.AsyncClient,
    telefono_cliente: str = None,
    aprobado: bool = None,
    precio_final: float = None,
    motivo_rechazo: str = None,
) -> str:
    """Ejecutado solo desde el flujo del número de cotización humano, nunca desde la
    conversación con el cliente."""
    client_digits = re.sub(r"\D", "", telefono_cliente or "")
    if not client_digits:
        return "No pude identificar el teléfono del cliente, inclúyelo de nuevo (solo dígitos)."

    order = storage.get_active_order(client_digits)
    matched_phone = client_digits
    if not order:
        last10 = client_digits[-10:]
        for candidate in (f"521{last10}", f"52{last10}", last10):
            order = storage.get_active_order(candidate)
            if order:
                matched_phone = candidate
                break

    if not order or order.get("status") != "cotizacion_pendiente_aprobacion":
        return f"No encontré una cotización pendiente de aprobación para el teléfono {telefono_cliente}."

    if not aprobado:
        storage.reject_quote(matched_phone, motivo_rechazo)
        logger.info("Cotización de %s RECHAZADA. Motivo: %s", matched_phone, motivo_rechazo or "-")
        await send_whatsapp_message(
            http_client, matched_phone,
            "Estamos ajustando tu cotización de envío, en un momento te la mando actualizada.",
        )
        return f"Cotización de {matched_phone} marcada como rechazada/por ajustar."

    options_override = None
    if precio_final is not None:
        original_options = json.loads(order.get("shipping_options_json") or "[]")
        eta = original_options[0]["eta"] if original_options else "por confirmar"
        options_override = json.dumps([{"nombre": "Cotización ajustada", "precio": precio_final, "eta": eta}])

    updated_order = storage.approve_quote(matched_phone, options_override)
    if not updated_order:
        return f"No pude aprobar la cotización de {matched_phone} (puede que ya no esté pendiente)."

    logger.info(
        "Cotización de %s APROBADA%s.", matched_phone,
        f" con precio final {precio_final}" if precio_final is not None else "",
    )

    options = json.loads(updated_order.get("shipping_options_json") or "[]")
    options_text = "\n".join(f"- {o['nombre']}: ${o['precio']} ({o['eta']})" for o in options)
    customer = storage.get_customer(matched_phone)
    name = customer.get("name") if customer else None
    saludo = f"{name}, " if name else ""
    await send_whatsapp_message(
        http_client, matched_phone,
        f"{saludo}¡ya tengo cotizado tu envío! Estas son tus opciones:\n\n{options_text}\n\n¿Cuál prefieres?",
    )
    return f"Cotización de {matched_phone} aprobada y enviada al cliente."


async def _choose_shipping(sender_phone: str, opcion: str = None) -> str:
    order = storage.get_active_order(sender_phone)
    if not order or order.get("status") != "cotizado":
        return "ERROR: no hay una cotización lista para elegir envío en este momento."

    options = json.loads(order.get("shipping_options_json") or "[]")
    chosen = next((o for o in options if o["nombre"].strip().lower() == (opcion or "").strip().lower()), None)
    if not chosen:
        nombres = ", ".join(o["nombre"] for o in options)
        return f"ERROR: no reconozco esa opción de envío. Las opciones válidas son: {nombres}."

    storage.set_shipping_choice(order["id"], chosen["nombre"], chosen["precio"], chosen["eta"])
    logger.info(
        "Cliente %s eligió envío '%s' ($%s) para la orden %s.",
        sender_phone, chosen["nombre"], chosen["precio"], order["id"],
    )

    product_amount = _parse_money(order.get("amount_expected"))
    shipping_amount = _parse_money(chosen["precio"])
    if product_amount is not None and shipping_amount is not None:
        total_text = f"${product_amount + shipping_amount:,.0f}"
    else:
        total_text = f"tu producto más ${chosen['precio']} de envío"

    return (
        f"OK: envío '{chosen['nombre']}' elegido, entrega estimada {chosen['eta']}. Copia este texto EXACTO "
        f"con los datos bancarios, sin cambiar ni un solo dígito de las cuentas o CLABEs, y menciona que el "
        f"monto total a pagar (producto + envío) es {total_text}:\n\n{BANK_DETAILS_TEXT}\n\nDespués de eso, "
        f"pídele al cliente su comprobante de pago (foto o captura)."
    )


async def _add_delivery_notes(sender_phone: str, notas: str = None) -> str:
    order = storage.get_active_order(sender_phone)
    if not order:
        return "ERROR: no hay ninguna cotización o pedido en curso para este cliente."
    storage.set_delivery_notes(order["id"], notas)
    logger.info("Notas de entrega guardadas para la orden %s de %s.", order["id"], sender_phone)
    return "OK: referencia de envío guardada."


async def _start_enrollment(
    sender_phone: str,
    numero_patrocinador: str = None,
    nombre_patrocinador: str = None,
    nombre_completo: str = None,
    edad: str = None,
) -> str:
    existing = storage.get_active_enrollment(sender_phone)
    if existing:
        return (
            "OK: ya hay una inscripción en curso para esta persona, no crees una nueva. Ayúdala a "
            "continuar desde donde se quedó (falta la foto de INE o la foto de rostro)."
        )
    enrollment_id = storage.create_enrollment(
        sender_phone,
        sponsor=numero_patrocinador,
        sponsor_name=nombre_patrocinador,
        full_name=nombre_completo,
        age=edad,
    )
    logger.info(
        "Inscripción %s creada para %s (patrocinador=%s %s, nombre=%s, edad=%s)",
        enrollment_id, sender_phone, nombre_patrocinador, numero_patrocinador, nombre_completo, edad,
    )
    return (
        "OK: datos registrados. Ahora pídele que mande la foto de su INE (identificación oficial). "
        "Aclárale que esto es solo para que el equipo la revise, no es una verificación automática."
    )


async def _forward_enrollment_to_team(http_client: httpx.AsyncClient, enrollment: dict) -> None:
    summary = (
        f"🆕 Nueva solicitud de inscripción — NO verificada automáticamente, confirmen identidad "
        f"antes de dar de alta.\n"
        f"Teléfono del aspirante: {enrollment['phone']}\n"
        f"Nombre completo: {enrollment.get('full_name') or 'no especificado'}\n"
        f"Edad: {enrollment.get('age') or 'no especificada'}\n"
        f"Patrocinador: {enrollment.get('sponsor_name') or 'no especificado'} "
        f"({enrollment.get('sponsor_distributor_number') or 'sin número'})\n\n"
        f"Se adjuntan foto de INE y foto de rostro para su revisión. Cuando la des de alta, avísale a TEO "
        f"con el teléfono del aspirante y en cuánto tiempo se refleja (usa confirmar_inscripcion)."
    )
    for recipient in (SALES_PHONE_NUMBER, CEDIS_PHONE_NUMBER):
        await send_whatsapp_message(http_client, recipient, summary)
        await _send_whatsapp_image_by_id(http_client, recipient, enrollment.get("ine_media_id"), "INE del aspirante")
        await _send_whatsapp_image_by_id(http_client, recipient, enrollment.get("face_media_id"), "Foto de rostro del aspirante")


async def _handle_enrollment_ine_photo(
    http_client: httpx.AsyncClient, sender_phone: str, image_data: dict, enrollment: dict
) -> None:
    media_id = image_data.get("id")
    if not media_id:
        return
    logger.info("Foto de INE recibida de %s (inscripción %s).", sender_phone, enrollment["id"])
    storage.log_message(sender_phone, "user", "[foto de INE]")
    storage.set_enrollment_ine(enrollment["id"], media_id)

    reply = "¡Gracias! Ahora mándame una foto de tu rostro tomada en este momento (no una foto vieja ni de galería)."
    storage.log_message(sender_phone, "assistant", reply)
    await send_whatsapp_message(http_client, sender_phone, reply)


async def _handle_enrollment_face_photo(
    http_client: httpx.AsyncClient, sender_phone: str, image_data: dict, enrollment: dict
) -> None:
    media_id = image_data.get("id")
    if not media_id:
        return
    logger.info("Foto de rostro recibida de %s (inscripción %s).", sender_phone, enrollment["id"])
    storage.log_message(sender_phone, "user", "[foto de rostro]")
    storage.set_enrollment_face(enrollment["id"], media_id)

    updated_enrollment = storage.get_enrollment(enrollment["id"])
    await _forward_enrollment_to_team(http_client, updated_enrollment)
    storage.mark_enrollment_sent(enrollment["id"])

    reply = (
        "¡Listo, ya tengo toda tu información! Nuestro equipo la va a revisar y se va a poner en "
        "contacto contigo para completar tu registro como socio."
    )
    storage.log_message(sender_phone, "assistant", reply)
    await send_whatsapp_message(http_client, sender_phone, reply)


async def _notify_ceo_of_purchase(http_client: httpx.AsyncClient, order: dict) -> None:
    """Alma (CEO) recibe un resumen de toda compra autorizada — sin la imagen del comprobante,
    eso es solo para Contraloría/logística."""
    customer = storage.get_customer(order["phone"])
    name = customer.get("name") if customer else None
    summary = (
        f"✅ Compra confirmada.\n"
        f"Cliente: {name or 'sin nombre'} ({order['phone']})\n"
        f"Producto: {order.get('product') or 'no especificado'}\n"
        f"Monto producto: {order.get('amount_expected') or 'no especificado'}\n"
        f"Envío: {order.get('shipping_choice') or 'no especificado'} (${order.get('shipping_cost') or '0'})\n"
        f"Entrega estimada: {order.get('delivery_estimate') or 'no especificada'}"
    )
    await send_whatsapp_message(http_client, CEO_PHONE_NUMBER, summary)


async def _authorize_order(
    http_client: httpx.AsyncClient,
    telefono_cliente: str = None,
    aprobado: bool = None,
    tiempo_entrega_estimado: str = None,
    motivo_rechazo: str = None,
) -> str:
    """Ejecutado solo desde el flujo del número de control humano (`_handle_control_message`),
    nunca desde la conversación con el cliente."""
    client_digits = re.sub(r"\D", "", telefono_cliente or "")
    if not client_digits:
        return "No pude identificar el teléfono del cliente, inclúyelo de nuevo (solo dígitos)."

    order = storage.get_active_order(client_digits)
    matched_phone = client_digits
    if not order:
        last10 = client_digits[-10:]
        for candidate in (f"521{last10}", f"52{last10}", last10):
            order = storage.get_active_order(candidate)
            if order:
                matched_phone = candidate
                break

    if not order:
        return f"No encontré un pedido en curso para el teléfono {telefono_cliente}. Verifica el número."

    if not aprobado:
        storage.reject_order(matched_phone, motivo_rechazo)
        logger.info("Pedido de %s RECHAZADO por control. Motivo: %s", matched_phone, motivo_rechazo or "-")
        await send_whatsapp_message(
            http_client, matched_phone,
            "Hola, revisamos tu comprobante de pago y tuvimos un problema para confirmarlo. Un asesor humano "
            "de I'AM se va a poner en contacto contigo para resolverlo.",
        )
        return f"Pedido de {matched_phone} marcado como rechazado."

    updated_order = storage.authorize_order(matched_phone, tiempo_entrega_estimado)
    if not updated_order:
        return f"No pude autorizar el pedido de {matched_phone} (verifica que exista un pedido en curso)."

    final_estimate = updated_order.get("delivery_estimate") or "en un plazo por confirmar"
    logger.info("Pedido de %s AUTORIZADO por control. Entrega estimada: %s", matched_phone, final_estimate)
    await _forward_order_to_logistics(http_client, updated_order)
    await _notify_ceo_of_purchase(http_client, updated_order)

    customer = storage.get_customer(matched_phone)
    name = customer.get("name") if customer else None
    saludo = f"{name}, " if name else ""
    await send_whatsapp_message(
        http_client, matched_phone,
        f"{saludo}¡tu pago fue confirmado con éxito! 🎉 Tu pedido va en camino, tiempo estimado de entrega: "
        f"{final_estimate}. Cualquier duda, aquí estoy.",
    )
    return f"Pedido de {matched_phone} autorizado. Entrega estimada: {final_estimate}. Ya avisé al cliente y a logística."


AUTHORIZE_ORDER_TOOL = {
    "type": "function",
    "function": {
        "name": "autorizar_pedido",
        "description": "Autoriza o rechaza el pago reportado por un cliente, según la decisión del humano de control.",
        "parameters": {
            "type": "object",
            "properties": {
                "telefono_cliente": {
                    "type": "string",
                    "description": "Teléfono del cliente mencionado por el humano, solo dígitos.",
                },
                "aprobado": {
                    "type": "boolean",
                    "description": "true si autoriza el pago, false si lo rechaza.",
                },
                "tiempo_entrega_estimado": {
                    "type": "string",
                    "description": "Tiempo estimado de entrega, solo si aprobado=true (ej. '3 a 5 días hábiles').",
                },
                "motivo_rechazo": {
                    "type": "string",
                    "description": "Motivo breve del rechazo, solo si aprobado=false.",
                },
            },
            "required": ["telefono_cliente", "aprobado"],
        },
    },
}

APPROVE_QUOTE_TOOL = {
    "type": "function",
    "function": {
        "name": "aprobar_cotizacion",
        "description": "Aprueba o rechaza una cotización de envío reportada, según la decisión del humano de cotización.",
        "parameters": {
            "type": "object",
            "properties": {
                "telefono_cliente": {
                    "type": "string",
                    "description": "Teléfono del cliente mencionado por el humano, solo dígitos.",
                },
                "aprobado": {
                    "type": "boolean",
                    "description": "true si aprueba la cotización, false si la rechaza.",
                },
                "precio_final": {
                    "type": "number",
                    "description": "Precio final ajustado, solo si el humano dio uno distinto al calculado.",
                },
                "motivo_rechazo": {
                    "type": "string",
                    "description": "Motivo breve, solo si aprobado=false.",
                },
            },
            "required": ["telefono_cliente", "aprobado"],
        },
    },
}

CONFIRM_ENROLLMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "confirmar_inscripcion",
        "description": (
            "Confirma que un aspirante ya fue dado de alta formalmente como socio, y en cuánto tiempo se "
            "reflejará su inscripción en el sistema."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "telefono_aspirante": {
                    "type": "string",
                    "description": "Teléfono del aspirante mencionado por el humano, solo dígitos.",
                },
                "tiempo_reflejo": {
                    "type": "string",
                    "description": "Tiempo estimado en que se reflejará la inscripción (ej. '24 horas', 'mismo día').",
                },
            },
            "required": ["telefono_aspirante", "tiempo_reflejo"],
        },
    },
}

INVENTORY_TOOL = {
    "type": "function",
    "function": {
        "name": "actualizar_inventario",
        "description": (
            "Actualiza la cantidad en existencia de un producto y notifica automáticamente a la CEO y "
            "Contraloría."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "producto": {"type": "string", "description": "Nombre del producto cuyo stock se actualiza."},
                "cantidad": {"type": "integer", "description": "Cantidad actual en existencia."},
            },
            "required": ["producto", "cantidad"],
        },
    },
}

REPORT_PRODUCTION_TOOL = {
    "type": "function",
    "function": {
        "name": "reportar_lote_produccion",
        "description": (
            "Registra un lote de producto terminado. Suma la cantidad al inventario, avisa automáticamente "
            "a Logística para que lo cuente/verifique y lo traslade a bodega, y notifica a la CEO y "
            "Contraloría."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "producto": {"type": "string", "description": "Nombre del producto terminado en este lote."},
                "cantidad": {"type": "integer", "description": "Cantidad de unidades del lote."},
                "banda_garantia": {
                    "type": "string",
                    "description": "Código/banda de garantía o seguridad del lote.",
                },
            },
            "required": ["producto", "cantidad", "banda_garantia"],
        },
    },
}

STAFF_CONTROL_PROMPT = """Eres un asistente interno (NO de cara a clientes) que ayuda al equipo de control de \
pagos de I'AM a autorizar o rechazar pagos reportados por clientes. Tu única función es interpretar el \
mensaje en español del humano y llamar a la función `autorizar_pedido` con: el teléfono del cliente que \
mencione (solo dígitos), si aprueba o rechaza el pago, y opcionalmente un tiempo de entrega si quiere \
sobreescribir el que el cliente ya eligió al cotizar su envío (normalmente no hace falta darlo de nuevo). Si \
no puedes identificar el teléfono del cliente en el mensaje, NO llames a la función: responde en texto \
pidiendo que lo incluya."""

STAFF_ARMANDO_PROMPT = """Eres un asistente interno (NO de cara a clientes) que ayuda a Armando (Logística, \
cotizaciones y puntos de entrega) de I'AM. Puedes hacer 2 cosas: (1) aprobar o rechazar una cotización de \
envío con `aprobar_cotizacion` (teléfono del cliente, aprobado/rechazado, precio final si lo ajusta), o (2) \
actualizar el inventario de un producto con `actualizar_inventario` (producto, cantidad en existencia). \
Interpreta el mensaje del humano y llama a la función que corresponda. Si es sobre una cotización y no \
puedes identificar el teléfono del cliente, NO llames a la función: responde en texto pidiendo que lo incluya."""

STAFF_YENI_PROMPT = """Eres un asistente interno (NO de cara a clientes) que ayuda a Yeni (CEDIS \
Chimalhuacán) de I'AM. Puedes hacer 2 cosas: (1) confirmar que ya diste de alta a un nuevo socio con \
`confirmar_inscripcion` (teléfono del aspirante, tiempo en que se refleja la inscripción), o (2) actualizar \
el inventario de un producto con `actualizar_inventario` (producto, cantidad en existencia). Interpreta el \
mensaje del humano y llama a la función que corresponda. Si no puedes identificar el teléfono del aspirante, \
NO llames a `confirmar_inscripcion`: responde en texto pidiendo que lo incluya."""

STAFF_BELEN_PROMPT = """Eres un asistente interno (NO de cara a clientes) que ayuda a Belén Figueroa (Ventas \
e inscripciones) de I'AM. Tu única función es interpretar el mensaje del humano y llamar a \
`confirmar_inscripcion` cuando diga que ya dio de alta a un nuevo socio, con el teléfono del aspirante y el \
tiempo en que se reflejará su inscripción. Si no puedes identificar el teléfono del aspirante, NO llames a \
la función: responde en texto pidiendo que lo incluya."""

STAFF_JACOB_PROMPT = """Eres un asistente interno (NO de cara a clientes) que ayuda a Jacob Riveros, quien \
tiene un rol doble en I'AM: Marketing e Inteligencia de Conversaciones, y también Jefe de Producción. Tu \
única función aquí es interpretar cuando te reporte un lote de producto terminado y llamar a \
`reportar_lote_produccion` con: el nombre del producto, la cantidad del lote, y la banda de garantía (código \
de seguridad del lote). Si falta alguno de esos 3 datos, NO llames a la función: responde en texto pidiendo \
el dato que falta."""


async def _handle_staff_message(
    http_client: httpx.AsyncClient, sender_phone: str, user_text: str, system_prompt: str, tools: list, dispatch: dict
) -> None:
    """Procesa mensajes de un número de staff autorizado (directorio oficial) — nunca pasa por
    el SYSTEM_PROMPT de cara al cliente, es un flujo interno separado. `dispatch` mapea nombre
    de función -> coroutine que recibe (http_client, **args) y devuelve el texto de resultado."""
    try:
        completion = await groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            tools=tools,
            temperature=0.2,
            max_tokens=300,
        )
        response_message = completion.choices[0].message

        if not response_message.tool_calls:
            reply = (response_message.content or "").strip() or (
                "No identifiqué qué quieres hacer. Sé más específico, por favor."
            )
            await send_whatsapp_message(http_client, sender_phone, reply)
            return

        tool_call = response_message.tool_calls[0]
        args = json.loads(tool_call.function.arguments)
        handler = dispatch.get(tool_call.function.name)
        if not handler:
            await send_whatsapp_message(http_client, sender_phone, f"No reconozco la función '{tool_call.function.name}'.")
            return
        result = await handler(http_client, **args)
        await send_whatsapp_message(http_client, sender_phone, result)
    except Exception:
        logger.exception("Error al procesar mensaje de staff de %s.", sender_phone)
        await send_whatsapp_message(http_client, sender_phone, "Hubo un error procesando tu instrucción, intenta de nuevo.")


async def _confirm_enrollment(
    http_client: httpx.AsyncClient, telefono_aspirante: str = None, tiempo_reflejo: str = None
) -> str:
    client_digits = re.sub(r"\D", "", telefono_aspirante or "")
    if not client_digits:
        return "No pude identificar el teléfono del aspirante, inclúyelo de nuevo (solo dígitos)."
    if not tiempo_reflejo:
        return "Necesito que me digas en cuánto tiempo se reflejará la inscripción (ej. '24 horas')."

    matched_phone = client_digits
    enrollment = storage.confirm_enrollment(matched_phone, tiempo_reflejo)
    if not enrollment:
        last10 = client_digits[-10:]
        for candidate in (f"521{last10}", f"52{last10}", last10):
            enrollment = storage.confirm_enrollment(candidate, tiempo_reflejo)
            if enrollment:
                matched_phone = candidate
                break

    if not enrollment:
        return f"No encontré una inscripción enviada para el teléfono {telefono_aspirante}. Verifica el número."

    logger.info("Inscripción de %s CONFIRMADA. Se refleja en: %s", matched_phone, tiempo_reflejo)
    customer = storage.get_customer(matched_phone)
    name = (customer.get("name") if customer else None) or enrollment.get("full_name")
    saludo = f"{name}, " if name else ""
    await send_whatsapp_message(
        http_client, matched_phone,
        f"{saludo}¡buenas noticias! Tu inscripción como socio de I'AM ya fue registrada por nuestro equipo. "
        f"Se verá reflejada en el sistema en aproximadamente {tiempo_reflejo}. ¡Bienvenido/a a I'AM!",
    )
    return f"Inscripción de {matched_phone} confirmada. Ya le avisé con el tiempo: {tiempo_reflejo}."


async def _broadcast_inventory_update(http_client: httpx.AsyncClient) -> None:
    """Reportes de inventario: destinatarios EXCLUSIVOS — CEO y Contraloría (Araceli y
    Benjamín). Nunca Ventas ni Marketing, por directiva explícita del negocio."""
    stock = storage.get_all_stock()
    lines = [f"- {item['product']}: {item['quantity']} unidades" for item in stock] or ["(sin productos registrados)"]
    summary = "📦 Inventario actualizado:\n" + "\n".join(lines)
    recipients = [CEO_PHONE_NUMBER] + list(CONTROL_PHONE_NUMBERS)
    for recipient in recipients:
        await send_whatsapp_message(http_client, recipient, summary)


async def _update_inventory(
    http_client: httpx.AsyncClient, updated_by: str = None, producto: str = None, cantidad=None
) -> str:
    if not producto or cantidad is None:
        return "Necesito el nombre del producto y la cantidad para actualizar el inventario."
    try:
        qty = int(cantidad)
    except (TypeError, ValueError):
        return "La cantidad debe ser un número entero."

    storage.upsert_stock(producto, qty, updated_by=updated_by)
    logger.info("Inventario actualizado por %s: %s -> %s unidades", updated_by, producto, qty)
    await _broadcast_inventory_update(http_client)
    return f"OK: inventario de '{producto}' actualizado a {qty} unidades. Ya avisé a la CEO y Contraloría."


async def _report_production_batch(
    http_client: httpx.AsyncClient,
    reported_by: str = None,
    producto: str = None,
    cantidad=None,
    banda_garantia: str = None,
) -> str:
    if not producto or cantidad is None or not banda_garantia:
        return "Necesito el producto, la cantidad del lote, y la banda de garantía para registrar el lote."
    try:
        qty = int(cantidad)
    except (TypeError, ValueError):
        return "La cantidad debe ser un número entero."

    batch_id = storage.create_production_batch(producto, qty, banda_garantia, reported_by)
    storage.increment_stock(producto, qty, updated_by=reported_by)
    logger.info(
        "Lote de producción %s registrado: %s x%s (banda %s) por %s",
        batch_id, producto, qty, banda_garantia, reported_by,
    )

    await send_whatsapp_message(
        http_client, LOGISTICS_PHONE_NUMBER,
        f"📦 Nuevo lote de producción terminado.\n"
        f"Producto: {producto}\n"
        f"Cantidad: {qty}\n"
        f"Banda de garantía: {banda_garantia}\n\n"
        f"Por favor acude a contar y verificar físicamente la cantidad entregada, y procede con su traslado "
        f"a la bodega.",
    )

    stock = storage.get_all_stock()
    stock_lines = [f"- {item['product']}: {item['quantity']} unidades" for item in stock] or ["(sin productos registrados)"]
    report = (
        f"🏭 Nuevo lote de producción registrado.\n"
        f"Producto: {producto}\n"
        f"Cantidad producida: {qty}\n"
        f"Banda de garantía: {banda_garantia}\n\n"
        f"Inventario actualizado:\n" + "\n".join(stock_lines)
    )
    for recipient in [CEO_PHONE_NUMBER] + list(CONTROL_PHONE_NUMBERS):
        await send_whatsapp_message(http_client, recipient, report)

    return (
        f"OK: lote de {producto} (x{qty}, banda {banda_garantia}) registrado. Ya avisé a logística y mandé "
        f"el reporte a la CEO y Contraloría."
    )


async def _handle_payment_proof(
    http_client: httpx.AsyncClient, sender_phone: str, image_data: dict, order: dict
) -> None:
    """Se llama solo cuando la orden ya está en 'pendiente_pago' (Etapa 2) — la ubicación ya
    se obtuvo en la Etapa 1 de cotización, así que el comprobante es lo único que falta."""
    media_id = image_data.get("id")
    if not media_id:
        return

    logger.info("Comprobante de pago recibido de %s (media id %s, orden %s).", sender_phone, media_id, order["id"])
    storage.log_message(sender_phone, "user", "[comprobante de pago]")

    try:
        image_bytes = await _download_whatsapp_media(http_client, media_id)
        mime_type = image_data.get("mime_type", "image/jpeg").split(";")[0]
        vision_summary = await vision.analyze_payment_proof(http_client, image_bytes, mime_type)
    except Exception:
        logger.exception("Error al analizar el comprobante de pago de %s.", sender_phone)
        vision_summary = "No se pudo leer la imagen automáticamente; un humano deberá revisarla directamente."

    storage.set_order_proof(order["id"], media_id, vision_summary)
    storage.mark_order_in_review(order["id"])
    updated_order = storage.get_order(order["id"])
    await _forward_order_to_control(http_client, updated_order)

    reply = (
        "¡Gracias! Ya tengo tu comprobante. Tu pago está en revisión, en cuanto se confirme te aviso con la "
        "fecha estimada de entrega."
    )
    storage.log_message(sender_phone, "assistant", reply)
    await send_whatsapp_message(http_client, sender_phone, reply)


async def _save_weekly_plan(
    http_client: httpx.AsyncClient,
    sender_phone: str,
    protocolo: str = None,
    suplemento_hora: str = None,
    comida_asociada: str = None,
    entrenamiento_hora: str = None,
    dias_descanso: list = None,
) -> str:
    """Ejecuta lo que pide la tool `guardar_plan_semanal`. Devuelve un mensaje de resultado
    (éxito o error) que se le pasa de vuelta al modelo como resultado de la tool — así TEO
    solo puede confirmar el plan al cliente si esto realmente tuvo éxito."""
    try:
        sup_hour, sup_minute = (int(part) for part in suplemento_hora.split(":"))
        ent_hour, ent_minute = (int(part) for part in entrenamiento_hora.split(":"))
    except (ValueError, AttributeError, TypeError):
        return "ERROR: hora inválida o faltante. Pide al cliente que confirme la hora exacta (ej. 8:00 o 8 de la mañana)."

    if not (0 <= sup_hour <= 23 and 0 <= sup_minute <= 59 and 0 <= ent_hour <= 23 and 0 <= ent_minute <= 59):
        return "ERROR: hora fuera de rango. Pide al cliente que confirme la hora exacta."

    if comida_asociada not in ("desayuno", "comida", "cena"):
        return "ERROR: falta saber con cuál comida se toma el suplemento (desayuno, comida o cena)."

    rest_days = set()
    for day_name in dias_descanso or []:
        index = plans.DAY_NAME_TO_INDEX.get(str(day_name).strip().lower())
        if index is not None:
            rest_days.add(index)

    storage.upsert_customer(sender_phone, protocol=protocolo)
    dosis_hour, dosis_minute = _add_minutes(sup_hour, sup_minute, 90)
    storage.set_reminders(
        sender_phone,
        [
            ("checkin_matutino", sup_hour, sup_minute, comida_asociada),
            ("recordatorio_dosis", dosis_hour, dosis_minute, None),
            ("educacion_receta", 14, 0, None),
            ("checkin_nocturno", 19, 0, None),
            ("resumen_semanal", 19, 0, None),
        ],
    )
    weekly_plan = plans.build_weekly_plan(rest_days)
    storage.set_daily_content(sender_phone, weekly_plan)

    await send_whatsapp_calendar_image(
        http_client,
        sender_phone,
        weekly_plan=weekly_plan,
        protocol_name=protocolo or "tu protocolo personalizado",
        sup_time=f"{sup_hour:02d}:{sup_minute:02d}",
        meal_label=comida_asociada,
        ent_time=f"{ent_hour:02d}:{ent_minute:02d}",
    )

    dias_str = ", ".join(plans.DAY_NAMES[d] for d in sorted(rest_days)) or "ninguno adicional (solo domingo)"
    logger.info(
        "Plan guardado (tool call) para %s: suplemento %02d:%02d con %s, entrenamiento %02d:%02d, descanso extra: %s",
        sender_phone, sup_hour, sup_minute, comida_asociada, ent_hour, ent_minute, dias_str,
    )
    return (
        f"OK: plan guardado correctamente y la imagen del calendario semanal ya fue enviada por WhatsApp. "
        f"Suplemento {sup_hour:02d}:{sup_minute:02d} junto con {comida_asociada}. Entrenamiento "
        f"{ent_hour:02d}:{ent_minute:02d}. Días de descanso adicionales: {dias_str}. A partir de mañana "
        f"recibirá su check-in matutino, recordatorio de dosis, tip de educación/receta y check-in nocturno "
        f"automáticamente. Menciónale que puede escribir PAUSA en cualquier momento para pausar estos mensajes, "
        f"y REANUDAR para reactivarlos."
    )


EXECUTIVE_REPORT_HOUR = 20  # 8pm — horario confirmado por el negocio
EXECUTIVE_REPORT_MINUTE = 0

MARKETING_INSIGHTS_PROMPT = """Eres un analista de marketing. A partir de esta muestra de mensajes reales \
de clientes de WhatsApp (rol 'user' son mensajes de clientes, rol 'assistant' son respuestas de TEO), \
identifica en español, en un mensaje breve para WhatsApp (máximo 8 líneas): 1) las 2-3 objeciones o dudas \
más frecuentes, 2) 1-2 tendencias o intereses que se repiten, 3) cualquier patrón que valga la pena que \
Marketing sepa. Si la muestra es muy pequeña o poco variada, dilo honestamente en vez de inventar patrones.

Muestra de conversación:
{sample}
"""

TRAINING_IDEAS_PROMPT = """Eres un diseñador de contenidos de capacitación para un equipo de venta directa. \
A partir de este resumen de objeciones y tendencias reales de clientes, sugiere en español, en un mensaje \
breve para WhatsApp (máximo 6 líneas), 2-3 ideas concretas de temas o dinámicas para una sesión de \
capacitación o reunión en línea que ayuden al equipo a manejar mejor esas objeciones o aprovechar esas \
tendencias.

Resumen de insights:
{insights}
"""


def _format_daily_metrics(metrics: dict, date_str: str) -> str:
    return (
        f"📊 Reporte ejecutivo diario — {date_str}\n"
        f"Cotizaciones creadas: {metrics['cotizaciones_creadas']}\n"
        f"Pagos autorizados: {metrics['pagos_autorizados']}\n"
        f"Ventas totales estimadas: ${metrics['ventas_totales_estimadas']:,.2f}\n"
        f"Inscripciones nuevas: {metrics['inscripciones_nuevas']}\n"
        f"Inscripciones confirmadas: {metrics['inscripciones_confirmadas']}"
    )


async def _generate_marketing_insights(sample: list) -> str:
    if not sample:
        return "No hay suficientes conversaciones recientes para generar insights hoy."
    sample_text = "\n".join(f"[{row['role']}] {row['content'][:200]}" for row in sample[-100:])
    try:
        completion = await groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "system", "content": MARKETING_INSIGHTS_PROMPT.format(sample=sample_text)}],
            temperature=0.4,
            max_tokens=400,
        )
        return completion.choices[0].message.content.strip()
    except Exception:
        logger.exception("Error al generar insights de marketing.")
        return "No se pudieron generar los insights de marketing hoy por un error técnico."


async def _generate_training_ideas(insights_text: str) -> str:
    try:
        completion = await groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "system", "content": TRAINING_IDEAS_PROMPT.format(insights=insights_text)}],
            temperature=0.5,
            max_tokens=300,
        )
        return completion.choices[0].message.content.strip()
    except Exception:
        logger.exception("Error al generar ideas de capacitación.")
        return "No se pudieron generar ideas de capacitación hoy por un error técnico."


async def _send_daily_executive_reports(http_client: httpx.AsyncClient) -> None:
    """Una vez al día: reporte de métricas a la CEO, insights de conversaciones a Marketing, e
    ideas de capacitación a Rubén, derivadas de esos mismos insights."""
    today_str = datetime.now(TIMEZONE).date().isoformat()

    metrics = storage.get_daily_metrics(today_str, exclude_phones=STAFF_PHONE_NUMBERS)
    report_text = _format_daily_metrics(metrics, today_str)
    await send_whatsapp_message(http_client, CEO_PHONE_NUMBER, report_text)
    logger.info("Reporte ejecutivo diario enviado a la CEO (%s).", today_str)

    sample = storage.get_recent_conversations_sample(200, exclude_phones=STAFF_PHONE_NUMBERS)
    insights_text = await _generate_marketing_insights(sample)
    await send_whatsapp_message(
        http_client, MARKETING_PHONE_NUMBER, f"📈 Insights de conversaciones ({today_str}):\n\n{insights_text}"
    )
    logger.info("Insights de marketing enviados a Jacob (%s).", today_str)

    training_ideas = await _generate_training_ideas(insights_text)
    await send_whatsapp_message(
        http_client, TRAINING_PHONE_NUMBER, f"🎓 Ideas de capacitación ({today_str}):\n\n{training_ideas}"
    )
    logger.info("Ideas de capacitación enviadas a Rubén (%s).", today_str)


async def run_executive_report_loop(http_client: httpx.AsyncClient) -> None:
    """Una vez al día (hora configurable), manda el reporte ejecutivo a la CEO, insights de
    marketing a Jacob, e ideas de capacitación a Rubén. Mismo patrón que run_reminder_loop
    (scheduler.py), pero vive en main.py porque necesita groq_client."""
    logger.info(
        "Loop de reportes ejecutivos iniciado (hora configurada: %02d:%02d).",
        EXECUTIVE_REPORT_HOUR, EXECUTIVE_REPORT_MINUTE,
    )
    last_sent_date = None
    while True:
        try:
            now = datetime.now(TIMEZONE)
            if (
                now.hour == EXECUTIVE_REPORT_HOUR
                and now.minute == EXECUTIVE_REPORT_MINUTE
                and last_sent_date != now.date()
            ):
                await _send_daily_executive_reports(http_client)
                last_sent_date = now.date()
        except Exception:
            logger.exception("Error inesperado en el loop de reportes ejecutivos.")
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(timeout=30.0)
    storage.init_db()
    app.state.scheduler_task = asyncio.create_task(
        run_reminder_loop(app.state.http_client, WHATSAPP_API_URL, WHATSAPP_TOKEN)
    )
    app.state.executive_report_task = asyncio.create_task(
        run_executive_report_loop(app.state.http_client)
    )
    logger.info("TEO iniciado correctamente. Modelo Groq: %s", GROQ_MODEL)
    yield
    app.state.scheduler_task.cancel()
    app.state.executive_report_task.cancel()
    await app.state.http_client.aclose()


app = FastAPI(title="TEO - Asistente WhatsApp de I'AM", lifespan=lifespan)


@app.get("/")
async def health_check():
    return {"status": "ok", "service": "TEO", "brand": "I'AM"}


@app.get("/webhook")
async def verify_webhook(request: Request):
    """Handshake de verificación exigido por Meta al configurar el webhook."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN:
        logger.info("Webhook verificado correctamente por Meta.")
        return PlainTextResponse(content=challenge, status_code=200)

    logger.warning("Intento de verificación de webhook fallido (mode=%s).", mode)
    return PlainTextResponse(content="Verificación fallida", status_code=403)


@app.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    """Responde a Meta de inmediato y procesa el mensaje en segundo plano.

    Meta reintenta la entrega si el webhook tarda o no responde 200, así que el
    procesamiento (Groq + envío a WhatsApp) nunca debe bloquear esta respuesta.
    """
    try:
        body = await request.json()
    except Exception:
        logger.warning("Payload de webhook no es JSON válido.")
        return {"status": "ignored"}

    background_tasks.add_task(process_webhook_event, body, request.app.state.http_client)
    return {"status": "received"}


async def process_webhook_event(body: dict, http_client: httpx.AsyncClient) -> None:
    try:
        value = body["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError, TypeError):
        return  # Payload con forma inesperada.

    messages = value.get("messages")
    if not messages:
        logger.info("Evento de webhook sin 'messages' (probable status callback): %s", value)
        return  # Notificaciones de estado (sent/delivered/read): no requieren acción.

    message = messages[0]
    sender_phone = message.get("from")
    message_id = message.get("id")

    if message_id and _already_processed(message_id):
        logger.info("Mensaje %s ya procesado, se ignora reintento de Meta.", message_id)
        return

    sender_normalized = _normalize_phone(sender_phone)
    is_staff_number = sender_normalized in STAFF_PHONE_NUMBERS

    if is_staff_number and message.get("type") == "text":
        toggle_text = message.get("text", {}).get("body", "").strip().lower()
        if toggle_text == "mi plan":
            storage.set_distributor_mode(sender_phone, True)
            await send_whatsapp_message(
                http_client, sender_phone,
                "Listo, a partir de ahora hablas conmigo como cualquier distribuidor: puedes pedir tu "
                "plan de optimización, resolver dudas del catálogo, etc. Lo que generes aquí se registra "
                "por separado de tu rol interno. Escribe 'modo staff' cuando quieras volver a tus "
                "comandos internos.",
            )
            return
        if toggle_text == "modo staff":
            storage.set_distributor_mode(sender_phone, False)
            await send_whatsapp_message(http_client, sender_phone, "Listo, volviste al modo de comandos internos.")
            return

    staff_distributor_active = is_staff_number and storage.is_distributor_mode(sender_phone)

    def _inventory_handler_for(staff_phone: str):
        async def _handler(http_client, **kw):
            return await _update_inventory(http_client, updated_by=staff_phone, **kw)
        return _handler

    def _production_handler_for(staff_phone: str):
        async def _handler(http_client, **kw):
            return await _report_production_batch(http_client, reported_by=staff_phone, **kw)
        return _handler

    staff_routes = [
        (
            {_normalize_phone(n) for n in CONTROL_PHONE_NUMBERS},
            STAFF_CONTROL_PROMPT,
            [AUTHORIZE_ORDER_TOOL],
            {"autorizar_pedido": _authorize_order},
        ),
        (
            {_normalize_phone(LOGISTICS_PHONE_NUMBER)},
            STAFF_ARMANDO_PROMPT,
            [APPROVE_QUOTE_TOOL, INVENTORY_TOOL],
            {"aprobar_cotizacion": _approve_quote, "actualizar_inventario": _inventory_handler_for(sender_phone)},
        ),
        (
            {_normalize_phone(CEDIS_PHONE_NUMBER)},
            STAFF_YENI_PROMPT,
            [CONFIRM_ENROLLMENT_TOOL, INVENTORY_TOOL],
            {"confirmar_inscripcion": _confirm_enrollment, "actualizar_inventario": _inventory_handler_for(sender_phone)},
        ),
        (
            {_normalize_phone(SALES_PHONE_NUMBER)},
            STAFF_BELEN_PROMPT,
            [CONFIRM_ENROLLMENT_TOOL],
            {"confirmar_inscripcion": _confirm_enrollment},
        ),
        (
            {_normalize_phone(MARKETING_PHONE_NUMBER)},
            STAFF_JACOB_PROMPT,
            [REPORT_PRODUCTION_TOOL],
            {"reportar_lote_produccion": _production_handler_for(sender_phone)},
        ),
    ]

    if not staff_distributor_active:
        for numbers, system_prompt, tools, dispatch in staff_routes:
            if sender_normalized in numbers:
                if message.get("type") == "text":
                    staff_text = message.get("text", {}).get("body", "").strip()
                    if staff_text:
                        await _handle_staff_message(http_client, sender_phone, staff_text, system_prompt, tools, dispatch)
                return

    if message.get("type") == "video":
        await _handle_exercise_video(http_client, sender_phone, message.get("video", {}))
        return

    if message.get("type") == "location":
        active_order = storage.get_active_order(sender_phone)
        if active_order and active_order.get("status") == "cotizando":
            await _handle_quote_location(http_client, sender_phone, message.get("location", {}), active_order)
        else:
            await send_whatsapp_message(
                http_client, sender_phone,
                "Gracias por tu ubicación. Por ahora no tengo ninguna cotización de envío esperándola — "
                "avísame si quieres que te cotice un pedido primero.",
            )
        return

    if message.get("type") == "image":
        active_order = storage.get_active_order(sender_phone)
        active_enrollment = storage.get_active_enrollment(sender_phone)
        if active_order and active_order.get("status") == "pendiente_pago" and not active_order.get("proof_media_id"):
            await _handle_payment_proof(http_client, sender_phone, message.get("image", {}), active_order)
        elif active_enrollment and not active_enrollment.get("ine_media_id"):
            await _handle_enrollment_ine_photo(http_client, sender_phone, message.get("image", {}), active_enrollment)
        elif active_enrollment and not active_enrollment.get("face_media_id"):
            await _handle_enrollment_face_photo(http_client, sender_phone, message.get("image", {}), active_enrollment)
        else:
            await _handle_bottle_photo(http_client, sender_phone)
        return

    if message.get("type") != "text":
        logger.info("Mensaje no soportado (%s) de %s.", message.get("type"), sender_phone)
        await send_whatsapp_message(
            http_client,
            sender_phone,
            "Por ahora puedo leer mensajes de texto, ubicaciones, fotos de tu frasco y videos de tu técnica "
            "de ejercicio. Cuéntame en palabras qué necesitas y con gusto te ayudo.",
        )
        return

    user_text = message.get("text", {}).get("body", "").strip()
    if not user_text:
        return

    normalized = user_text.strip().lower()
    if normalized in ("pausa", "reanudar"):
        await _handle_pause_command(http_client, sender_phone, normalized)
        return

    logger.info("Mensaje recibido de %s: %s", sender_phone, user_text)

    bot_reply = await get_ai_response(http_client, sender_phone, user_text)
    logger.info("Respuesta generada por TEO para %s: %s", sender_phone, bot_reply)

    await send_whatsapp_message(http_client, sender_phone, bot_reply)


async def get_ai_response(http_client: httpx.AsyncClient, sender_phone: str, user_text: str) -> str:
    history = _get_history(sender_phone)
    history.append({"role": "user", "content": user_text})
    storage.log_message(sender_phone, "user", user_text)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]

    for attempt in (1, 2):
        try:
            chat_completion = await groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                tools=ALL_TOOLS,
                temperature=0.5,
                max_tokens=1000,
            )
            response_message = chat_completion.choices[0].message

            if response_message.tool_calls:
                reply = await _run_tool_and_get_final_reply(http_client, sender_phone, messages, response_message)
            else:
                reply = response_message.content.strip()

            history.append({"role": "assistant", "content": reply})
            storage.log_message(sender_phone, "assistant", reply)
            return reply
        except BadRequestError:
            # El modelo a veces intenta llamar a guardar_plan_semanal sin tener los datos
            # completos (tool call mal disparado) y Groq rechaza la petición. Reintentamos
            # la misma conversación sin la tool para que al menos conteste en texto plano.
            logger.warning("Groq rechazó un tool call mal formado, reintentando sin tools...")
            try:
                fallback_completion = await groq_client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=messages,
                    temperature=0.5,
                    max_tokens=1000,
                )
                reply = fallback_completion.choices[0].message.content.strip()
                history.append({"role": "assistant", "content": reply})
                storage.log_message(sender_phone, "assistant", reply)
                return reply
            except Exception:
                logger.exception("El reintento sin tools también falló.")
                break
        except RateLimitError:
            if attempt == 1:
                logger.warning("Rate limit de Groq alcanzado, reintentando en unos segundos...")
                await asyncio.sleep(8)
                continue
            logger.exception("Rate limit de Groq persiste tras reintento.")
        except Exception:
            logger.exception("Error al consultar la API de Groq.")
            break

    history.pop()  # no dejar el mensaje del usuario sin respuesta en el historial
    return (
        "Estoy teniendo un problema técnico para procesar tu mensaje. Intenta de nuevo en unos "
        "minutos o escribe 'asesor' para hablar con nuestro equipo."
    )


async def _dispatch_tool_call(http_client: httpx.AsyncClient, sender_phone: str, tool_call) -> str:
    try:
        args = json.loads(tool_call.function.arguments)
    except (json.JSONDecodeError, TypeError):
        args = {}

    name = tool_call.function.name
    if name == "guardar_plan_semanal":
        return await _save_weekly_plan(http_client, sender_phone, **args)
    if name == "guardar_nombre":
        return await _save_customer_name(sender_phone, **args)
    if name == "escalar_a_humano":
        return await _escalate_to_human(sender_phone, **args)
    if name == "plantar_semilla_negocio":
        return await _plant_business_seed(sender_phone, **args)
    if name == "iniciar_curso_compartir":
        return await _start_share_course(sender_phone, **args)
    if name == "avanzar_curso_compartir":
        return await _advance_share_course(sender_phone, **args)
    if name == "iniciar_cotizacion":
        return await _start_quote(sender_phone, **args)
    if name == "elegir_envio":
        return await _choose_shipping(sender_phone, **args)
    if name == "agregar_referencia_envio":
        return await _add_delivery_notes(sender_phone, **args)
    if name == "iniciar_inscripcion":
        return await _start_enrollment(sender_phone, **args)
    if name == "mostrar_catalogo_visual":
        return await _send_catalog_infographic(http_client, sender_phone, **args)
    return f"ERROR: función '{name}' desconocida."


async def _run_tool_and_get_final_reply(
    http_client: httpx.AsyncClient, sender_phone: str, messages: list, response_message
) -> str:
    """Ejecuta la(s) tool(s) que pidió el modelo y le devuelve el resultado real (éxito o error) como
    mensaje(s) de rol 'tool', para que la respuesta final en lenguaje natural solo pueda confirmar
    una acción si de verdad se ejecutó — elimina la alucinación de 'listo, ya se envió'."""
    tool_calls = response_message.tool_calls

    followup_messages = messages + [
        {
            "role": "assistant",
            "content": response_message.content,
            "tool_calls": [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {"name": tool_call.function.name, "arguments": tool_call.function.arguments},
                }
                for tool_call in tool_calls
            ],
        },
    ]
    for tool_call in tool_calls:
        tool_result = await _dispatch_tool_call(http_client, sender_phone, tool_call)
        followup_messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": tool_result})
    final_completion = await groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=followup_messages,
        temperature=0.5,
        max_tokens=1000,
    )
    return final_completion.choices[0].message.content.strip()


async def send_whatsapp_message(http_client: httpx.AsyncClient, to: str, text: str) -> None:
    if not to:
        return

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text[:WHATSAPP_MAX_MESSAGE_LENGTH]},
    }

    try:
        response = await http_client.post(WHATSAPP_API_URL, headers=headers, json=payload)
        if response.status_code >= 400:
            logger.error("Error de la API de WhatsApp (%s): %s", response.status_code, response.text)
    except httpx.HTTPError:
        logger.exception("Fallo de red al enviar mensaje a WhatsApp.")


async def send_whatsapp_calendar_image(http_client: httpx.AsyncClient, to: str, weekly_plan: list, **kwargs) -> None:
    """Genera la imagen del calendario semanal y la envía como imagen de WhatsApp."""
    if not to:
        return

    try:
        image_bytes = render_weekly_calendar(weekly_plan=weekly_plan, **kwargs)
    except Exception:
        logger.exception("No se pudo generar la imagen del calendario semanal.")
        return

    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    try:
        upload_response = await http_client.post(
            WHATSAPP_MEDIA_URL,
            headers=headers,
            data={"messaging_product": "whatsapp", "type": "image/png"},
            files={"file": ("calendario.png", image_bytes, "image/png")},
        )
        if upload_response.status_code >= 400:
            logger.error("Error al subir la imagen del calendario (%s): %s", upload_response.status_code, upload_response.text)
            return
        media_id = upload_response.json().get("id")

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "image",
            "image": {"id": media_id, "caption": "Tu calendario semanal de optimización 📅"},
        }
        send_response = await http_client.post(WHATSAPP_API_URL, headers={**headers, "Content-Type": "application/json"}, json=payload)
        if send_response.status_code >= 400:
            logger.error("Error al enviar la imagen del calendario (%s): %s", send_response.status_code, send_response.text)
    except httpx.HTTPError:
        logger.exception("Fallo de red al enviar la imagen del calendario.")


_CATALOG_IMAGE_BYTES: bytes = None


async def _send_catalog_infographic(http_client: httpx.AsyncClient, sender_phone: str, **_ignored) -> str:
    """Envía la infografía estática del catálogo de protocolos. La imagen se renderiza una sola
    vez (no depende del cliente) y se reutiliza en memoria para el resto de la ejecución."""
    global _CATALOG_IMAGE_BYTES
    if _CATALOG_IMAGE_BYTES is None:
        _CATALOG_IMAGE_BYTES = render_catalog_infographic()

    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    try:
        upload_response = await http_client.post(
            WHATSAPP_MEDIA_URL,
            headers=headers,
            data={"messaging_product": "whatsapp", "type": "image/png"},
            files={"file": ("catalogo.png", _CATALOG_IMAGE_BYTES, "image/png")},
        )
        if upload_response.status_code >= 400:
            logger.error(
                "Error al subir la infografía del catálogo (%s): %s", upload_response.status_code, upload_response.text
            )
            return "No pude generar la imagen del catálogo justo ahora, pero puedo explicarte los protocolos en texto."
        media_id = upload_response.json().get("id")

        payload = {
            "messaging_product": "whatsapp",
            "to": sender_phone,
            "type": "image",
            "image": {"id": media_id, "caption": "Catálogo de protocolos I'AM 📋"},
        }
        send_response = await http_client.post(
            WHATSAPP_API_URL, headers={**headers, "Content-Type": "application/json"}, json=payload
        )
        if send_response.status_code >= 400:
            logger.error(
                "Error al enviar la infografía del catálogo (%s): %s", send_response.status_code, send_response.text
            )
            return "No pude enviar la imagen del catálogo justo ahora, pero puedo explicarte los protocolos en texto."
    except httpx.HTTPError:
        logger.exception("Fallo de red al enviar la infografía del catálogo.")
        return "No pude enviar la imagen del catálogo justo ahora, pero puedo explicarte los protocolos en texto."

    return "OK: imagen del catálogo enviada al cliente."


async def _download_whatsapp_media(http_client: httpx.AsyncClient, media_id: str):
    """Descarga un archivo de medios de WhatsApp (video/imagen) a partir de su media id."""
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    meta_response = await http_client.get(f"https://graph.facebook.com/{GRAPH_API_VERSION}/{media_id}", headers=headers)
    meta_response.raise_for_status()
    media_url = meta_response.json()["url"]

    file_response = await http_client.get(media_url, headers=headers, timeout=60.0)
    file_response.raise_for_status()
    return file_response.content


async def _handle_exercise_video(http_client: httpx.AsyncClient, sender_phone: str, video_data: dict) -> None:
    media_id = video_data.get("id")
    if not media_id:
        return

    logger.info("Video de técnica recibido de %s (media id %s).", sender_phone, media_id)
    storage.log_message(sender_phone, "user", "[video de técnica de ejercicio]")

    try:
        now = datetime.now(TIMEZONE)
        content = storage.get_daily_content(sender_phone, now.weekday())
        exercises_today = get_exercises_for_focus(content["training_focus"]) if content else []
        if exercises_today:
            context = "Hoy, según su plan, le tocaba practicar: " + "; ".join(
                f"{name} ({cue})" for name, _url, cue in exercises_today
            )
        else:
            context = "No hay información de qué ejercicio específico debía practicar hoy."

        video_bytes = await _download_whatsapp_media(http_client, media_id)
        mime_type = video_data.get("mime_type", "video/mp4").split(";")[0]

        feedback = await vision.analyze_exercise_video(http_client, video_bytes, mime_type, context)
    except Exception:
        logger.exception("Error al analizar el video de técnica de %s.", sender_phone)
        feedback = (
            "No pude analizar tu video en este momento. Intenta de nuevo en unos minutos, o si prefieres, "
            "un asesor humano puede revisarlo directamente."
        )

    logger.info("Feedback de técnica para %s: %s", sender_phone, feedback)
    storage.log_message(sender_phone, "assistant", feedback)
    await send_whatsapp_message(http_client, sender_phone, feedback)


async def _handle_bottle_photo(http_client: httpx.AsyncClient, sender_phone: str) -> None:
    """Reconoce la foto de evidencia del frasco (sin análisis de IA) y registra el check-in de dosis."""
    logger.info("Foto de frasco recibida de %s.", sender_phone)
    storage.log_message(sender_phone, "user", "[foto de frasco/evidencia]")

    today_str = datetime.now(TIMEZONE).date().isoformat()
    storage.record_checkin(sender_phone, today_str, "dosis", responded=True)

    customer = storage.get_customer(sender_phone)
    name = customer.get("name") if customer else None
    saludo = f"{name}, " if name else ""
    reply = f"{saludo}¡así se hace! Ya quedó registrada tu evidencia de hoy. Sigue así, a tu ritmo. 💪"

    storage.log_message(sender_phone, "assistant", reply)
    await send_whatsapp_message(http_client, sender_phone, reply)


async def _handle_pause_command(http_client: httpx.AsyncClient, sender_phone: str, command: str) -> None:
    """Maneja PAUSA/REANUDAR de forma determinística, sin pasar por Groq."""
    paused = command == "pausa"
    storage.set_paused(sender_phone, paused)
    storage.log_message(sender_phone, "user", command.upper())

    if paused:
        reply = "Listo, pausé tus recordatorios automáticos. Escribe REANUDAR cuando quieras que los retome."
    else:
        reply = "Listo, reactivé tus recordatorios automáticos."

    logger.info("%s procesó %s para %s.", "PAUSA" if paused else "REANUDAR", command.upper(), sender_phone)
    storage.log_message(sender_phone, "assistant", reply)
    await send_whatsapp_message(http_client, sender_phone, reply)
