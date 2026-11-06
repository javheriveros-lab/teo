"""Alta de nuevos socios con pago, verificación de identidad y activación.

Dos orígenes, un mismo camino:
- patrocinador: un distribuidor existente inscribe a otra persona (da su ID de empresario, paga la
  cuota y manda el comprobante; TEO contacta al invitado para pedirle INE y selfie).
- directo: un prospecto sin patrocinador paga su propia inscripción.

Etapas (`enrollments.flow_stage`):
  pago_pendiente → pago_en_revision → identidad_pendiente → revision_identidad → activo
  salidas: pago_rechazado (Ara/Benjamín), rechazado (Belén/Yeni); `pedir_fotos` regresa a identidad_pendiente.

Separación de responsabilidades: el pago lo confirma Control (Ara/Benjamín) con `autorizar_pedido`;
la identidad y el alta las aprueba Belén o Yeni con `revisar_alta_socio`. Las cifras (cuota, regalo,
descuento) salen de pricing.py, nunca del modelo. main.py inyecta los envíos con `configure()`.
"""
import logging
import re

import httpx

import pricing
import storage

logger = logging.getLogger("teo.altas")

TEMPLATE_ALTA_SOCIO = "teo_alta_socio"
TEO_DISPLAY_NUMBER = "+52 1 220 556 2940"
REVIEW_ACTIONS = ("aprobar", "rechazar", "pedir_fotos")
MAX_MEMBER_DISCOUNT_PCT = 60

_cfg = {
    "api_url": None, "token": None,
    "send_text": None,          # async (http, to, text) -> None
    "send_image": None,         # async (http, to, media_id, caption) -> None
    "bank_text": "",
    "reviewers": (),            # números de Belén y Yeni
    "staff_phones": set(),      # últimos 10 dígitos de todo el staff
    "remember": None,           # (phone, text) -> None: deja el mensaje en el historial del cliente
    "begin_course": None,       # (phone) -> str | None: contenido del día 1 del curso
    "log_review": None,         # (text) -> None: deja el paquete en el historial de Belén
}


class InvalidProspectPhone(ValueError):
    """El WhatsApp del invitado no es usable; el mensaje explica por qué."""


def configure(**kwargs) -> None:
    unknown = set(kwargs) - set(_cfg)
    if unknown:
        raise ValueError(f"Opciones desconocidas: {unknown}")
    _cfg.update(kwargs)


def _money(value: float) -> str:
    return f"${value:,.2f}".replace(".00", "")


def _offer() -> dict:
    return pricing.enrollment_offer()


def _last10(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")[-10:]


# ---------------------------------------------------------------------------
# Validaciones
# ---------------------------------------------------------------------------

def normalize_prospect_phone(raw: str, sponsor_phone: str = None) -> str:
    """Devuelve el número del invitado listo para WhatsApp (52 + 10 dígitos) o lanza
    InvalidProspectPhone con el motivo."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 13 and digits.startswith("521"):
        digits = digits[3:]
    elif len(digits) == 12 and digits.startswith("52"):
        digits = digits[2:]
    if len(digits) != 10:
        raise InvalidProspectPhone(
            "el número debe tener 10 dígitos de México (puede incluir +52); revisa que no falte ni sobre ninguno"
        )
    if sponsor_phone and digits == _last10(sponsor_phone):
        raise InvalidProspectPhone("es el mismo número de quien patrocina; necesito el WhatsApp de la persona invitada")
    if digits in _cfg["staff_phones"]:
        raise InvalidProspectPhone("ese número pertenece al equipo interno de I'AM")
    if storage.get_member(digits):
        raise InvalidProspectPhone("esa persona ya es socia activa de I'AM")
    if storage.get_open_enrollment_for_prospect(digits):
        raise InvalidProspectPhone("esa persona ya tiene un alta en proceso")
    return "52" + digits


# ---------------------------------------------------------------------------
# Textos fijos (cifras desde pricing.py)
# ---------------------------------------------------------------------------

def benefits_line() -> str:
    o = _offer()
    gift_value = f" (valor de {_money(o['gift_value'])})" if o["gift_value"] else ""
    return (f"*{o['gift']} gratis* de bienvenida{gift_value} y *{o['discount_pct']}% de descuento de por vida* "
            f"en todo el catálogo I'AM")


def offer_summary() -> str:
    """Una línea con las cifras de la membresía, para que TEO las diga sin inventarlas."""
    o = _offer()
    return (f"cuota única de inscripción {_money(o['fee'])} MXN, que incluye {o['gift']} gratis de bienvenida"
            + (f" (valor de {_money(o['gift_value'])})" if o["gift_value"] else "")
            + f" y {o['discount_pct']}% de descuento de por vida en todo el catálogo I'AM")


def payment_instructions(sponsored: bool) -> str:
    o = _offer()
    who = "de tu invitado" if sponsored else ""
    return (
        f"Para iniciar la inscripción {who} el pago es de *{_money(o['fee'])} MXN*, que incluye {benefits_line()}."
        f"\n\nEstos son los datos para tu depósito o transferencia:\n\n{_cfg['bank_text']}\n\n"
        f"En cuanto lo hagas, mándame por aquí la captura de tu comprobante."
    ).replace("inscripción  el", "inscripción el")


def ping_text(prospect_name: str, sponsor_name: str) -> str:
    return (
        f"¡Hola {prospect_name}! 🚀 {sponsor_name} solicitó tu alta como nuevo socio en I'AM. Para activar tu "
        f"cuenta y asegurar tu membresía, necesitamos validar tu identidad. Por favor reenvíame en este chat: "
        f"1) Foto clara de tu INE/Identificación por el frente. 2) Una selfie sosteniendo tu identificación al "
        f"lado de tu rostro."
    )


ADDRESS_REQUEST = "tu dirección completa (calle, número, colonia, código postal y ciudad)"


def identity_request_text(name: str = None, needs_address: bool = False) -> str:
    saludo = f"{name}, " if name else ""
    return (
        f"{saludo}¡tu pago ya fue confirmado! 🎉 Para activar tu cuenta y asegurar tu membresía, necesitamos "
        f"validar tu identidad. Por favor envíame en este chat: 1) Foto clara de tu INE/Identificación por el "
        f"frente. 2) Una selfie sosteniendo tu identificación al lado de tu rostro."
        + (f" 3) Escríbeme {ADDRESS_REQUEST}." if needs_address else "")
    )


def welcome_text(name: str = None, discount_pct: float = None, empresario_id: str = None) -> str:
    o = _offer()
    pct = f"{discount_pct:g}" if discount_pct is not None else str(o["discount_pct"])
    saludo = f"¡Bienvenid@ a I'AM, {name}! 🎉" if name else "¡Bienvenid@ a I'AM! 🎉"
    id_block = (
        f"Tu *ID de empresario I'AM es: {empresario_id}*\nGuárdalo muy bien: es tu identificación como socio "
        f"para tus compras, tu red y tus comisiones. Si algún día se te olvida, pregúntamelo y te lo recuerdo.\n\n"
        if empresario_id else ""
    )
    return (
        f"{saludo} Tu alta como socio ya quedó activa.\n\n"
        + id_block +
        f"Tus beneficios:\n"
        f"• *{pct}% de descuento de por vida* en todo el catálogo: se aplica solo en cada compra "
        f"que hagas conmigo, sin que me vuelvas a dar tu ID.\n"
        f"• Tu *{o['gift']} de regalo*"
        + (f" (valor de {_money(o['gift_value'])})" if o["gift_value"] else "")
        + ".\n\nPara enviarte tu Combo Detox, compárteme la ubicación exacta de entrega con el clip 📎 → "
        "Ubicación; te cotizo el envío y te paso las opciones.\n\n"
        "Y hoy mismo arrancamos tu entrenamiento desde cero 👇"
    )


def sponsor_activated_text(prospect_name: str) -> str:
    return (f"🎉 ¡Buenas noticias! {prospect_name} ya fue activad@ como socio en tu red de I'AM. Gracias por "
            f"compartir la transformación; ya empezó su entrenamiento conmigo.")


def control_line(order: dict) -> str:
    """Línea extra para el aviso de pago a Control cuando la orden es una inscripción."""
    if order.get("kind") != "inscripcion":
        return ""
    enrollment = storage.get_enrollment_by_order(order["id"]) or {}
    if enrollment.get("origin") == "patrocinador":
        return (f"Tipo: INSCRIPCIÓN de nuevo socio, pagada por el patrocinador "
                f"(ID {enrollment.get('sponsor_distributor_number') or 'sin ID'}"
                f"{', ' + enrollment['sponsor_name'] if enrollment.get('sponsor_name') else ''}).\n")
    return "Tipo: INSCRIPCIÓN directa de nuevo socio.\n"


def _review_package(enrollment: dict) -> str:
    approver = enrollment.get("payment_approved_by") or ""
    approver_text = f"Control (número terminado en {approver[-4:]})" if approver else "Control"
    if enrollment.get("origin") == "patrocinador":
        sponsor = (f"{enrollment.get('sponsor_name') or 'sin nombre'} · ID de empresario "
                   f"{enrollment.get('sponsor_distributor_number') or 'sin ID'} · tel. {enrollment.get('sponsor_phone')}")
    elif enrollment.get("sponsor_distributor_number"):
        known = (f"{enrollment.get('sponsor_name') or 'sin nombre'} · tel. {enrollment['sponsor_phone']}"
                 if enrollment.get("sponsor_phone") else "⚠️ ese ID no está registrado en TEO, confírmalo")
        sponsor = f"ID de empresario {enrollment['sponsor_distributor_number']} (lo dio el aspirante) · {known}"
    else:
        sponsor = "Inscripción directa (sin patrocinador)"
    return (
        f"🪪 *Alta de nuevo socio — revisión de identidad*\n"
        f"Aspirante: {enrollment.get('full_name') or 'sin nombre'} ({enrollment['phone']})\n"
        f"Dirección: {enrollment.get('address') or 'no la dio'}\n"
        f"Patrocinador: {sponsor}\n"
        f"Pago de {_money(_offer()['fee'])}: ✅ confirmado por {approver_text}\n\n"
        f"Revisa que la INE sea legible, que la selfie muestre a la misma persona sosteniéndola y que los datos "
        f"coincidan (si ves un intento de estafa, recházala). Responde:\n"
        f"• \"aprobar {enrollment['phone']}, ID <ID de empresario que le asignó I'AM>, <%> de descuento\" "
        f"(normalmente {_offer()['discount_pct']}%) para activarlo con Combo Detox + entrenamiento\n"
        f"• \"pedir fotos {enrollment['phone']}: <motivo>\" si no se ven bien\n"
        f"• \"rechazar {enrollment['phone']}: <motivo>\" si no procede"
    )


# ---------------------------------------------------------------------------
# Envíos
# ---------------------------------------------------------------------------

async def _post(http, payload: dict):
    """Envía a WhatsApp y devuelve el id del mensaje (wamid) o None si falló."""
    headers = {"Authorization": f"Bearer {_cfg['token']}", "Content-Type": "application/json"}
    try:
        response = await http.post(_cfg["api_url"], headers=headers, json=payload)
    except httpx.HTTPError:
        logger.exception("Fallo de red enviando a %s.", payload.get("to"))
        return None
    if response.status_code >= 400:
        logger.error("WhatsApp rechazó el envío a %s (%s): %s", payload.get("to"), response.status_code,
                     getattr(response, "text", ""))
        return None
    try:
        return (response.json().get("messages") or [{}])[0].get("id") or "sin-id"
    except Exception:
        return "sin-id"


async def _say(http, phone: str, text: str) -> None:
    await _cfg["send_text"](http, phone, text)
    if _cfg["remember"]:
        _cfg["remember"](phone, text)


async def send_ping(http, enrollment: dict) -> bool:
    """Primer contacto con el invitado: plantilla (nunca ha escrito a TEO) y, si falla, texto."""
    sponsor_name = enrollment.get("sponsor_name") or "Tu patrocinador"
    name = (enrollment.get("full_name") or "").split(" ")[0] or "hola"
    to = enrollment["phone"]
    template = {
        "messaging_product": "whatsapp", "to": to, "type": "template",
        "template": {"name": TEMPLATE_ALTA_SOCIO, "language": {"code": "es_MX"},
                     "components": [{"type": "body", "parameters": [{"type": "text", "text": name},
                                                                    {"type": "text", "text": sponsor_name}]}]},
    }
    message_id = await _post(http, template)
    if not message_id:
        message_id = await _post(http, {"messaging_product": "whatsapp", "to": to, "type": "text",
                                        "text": {"body": ping_text(name, sponsor_name)}})
    storage.set_enrollment_ping(enrollment["id"], message_id)
    if message_id:
        logger.info("Ping de alta enviado a %s (alta %s, msg %s).", to, enrollment["id"], message_id)
        return True
    await _notify_ping_failure(http, storage.get_enrollment(enrollment["id"]), "WhatsApp no aceptó el envío")
    return False


async def _notify_ping_failure(http, enrollment: dict, reason: str) -> None:
    if not storage.mark_ping_failure_notified(enrollment["id"]):
        return
    logger.warning("No se pudo contactar al invitado %s (alta %s): %s", enrollment["phone"], enrollment["id"], reason)
    if enrollment.get("sponsor_phone"):
        await _say(
            http, enrollment["sponsor_phone"],
            f"⚠️ No pude contactar a {enrollment.get('full_name') or 'tu invitado'} al número {enrollment['phone']} "
            f"({reason}). Verifica que el número sea correcto y tenga WhatsApp, o pídele que me escriba \"Hola\" "
            f"al {TEO_DISPLAY_NUMBER} y yo continúo con su alta desde ahí.",
        )


async def on_message_status(http, status: dict) -> None:
    """Aviso de estado de Meta (webhook). Si un ping de alta falló al entregarse, avisa al
    patrocinador una sola vez."""
    if status.get("status") != "failed":
        return
    enrollment = storage.get_enrollment_by_ping(status.get("id"))
    if not enrollment or enrollment.get("flow_stage") != "identidad_pendiente":
        return
    error = (status.get("errors") or [{}])[0]
    reason = error.get("title") or error.get("message") or f"error {error.get('code', 'desconocido')}"
    await _notify_ping_failure(http, enrollment, f"WhatsApp no lo entregó: {reason}")


# ---------------------------------------------------------------------------
# Herramientas del cliente
# ---------------------------------------------------------------------------

def _block_if_busy(payer_phone: str):
    order = storage.get_active_order(payer_phone)
    if order:
        return ("ERROR: este cliente tiene un pedido o pago en curso. Ayúdalo a terminarlo primero (o a que se "
                "rechace) antes de iniciar una inscripción, para no mezclar los pagos.")
    return None


def _product_label() -> str:
    o = _offer()
    return f"Inscripción I'AM (membresía {o['discount_pct']}% + {o['gift']} de regalo)"


async def start_sponsored(http, sender_phone: str, id_empresario: str = None, nombre_patrocinador: str = None,
                          nombre_invitado: str = None, telefono_invitado: str = None, **_ignored) -> str:
    sponsor_id = (id_empresario or "").strip()
    if len(re.sub(r"\W", "", sponsor_id)) < 3:
        return ("ERROR: para inscribir a un nuevo socio necesito obligatoriamente el ID de empresario I'AM de quien "
                "patrocina. Pídeselo antes de continuar.")
    existing = storage.get_open_enrollment_by_sponsor(sender_phone)
    if existing:
        return (f"OK: ya tiene una inscripción en curso (etapa: {existing['flow_stage']}) para "
                f"{existing.get('full_name') or 'su invitado'}. No abras otra; ayúdalo a continuar esa.")
    busy = _block_if_busy(sender_phone)
    if busy:
        return busy
    customer = storage.get_customer(sender_phone) or {}
    sponsor_name = (nombre_patrocinador or customer.get("name") or "").strip() or None
    enrollment_id = storage.create_enrollment_flow("patrocinador", sponsor_phone=sender_phone, sponsor_id=sponsor_id,
                                                   sponsor_name=sponsor_name)
    storage.create_enrollment_payment_order(sender_phone, enrollment_id, _product_label(),
                                            _money(_offer()["fee"]))
    logger.info("Alta patrocinada %s iniciada por %s (ID %s).", enrollment_id, sender_phone, sponsor_id)
    # El texto de pago lo manda el sistema tal cual (el modelo omitía líneas de los datos bancarios).
    await _say(http, sender_phone, payment_instructions(sponsored=True))
    result = ("OK: inscripción iniciada y YA le envié A ESTE CLIENTE (quien patrocina y paga) el texto con la cuota, "
              "los beneficios y los datos bancarios. NO repitas cifras ni datos bancarios. ")
    if nombre_invitado and telefono_invitado:
        return result + "Sobre su invitado: " + await register_prospect(
            http, sender_phone, nombre_completo=nombre_invitado, telefono_whatsapp=telefono_invitado)
    return result + (
        "Ahora pídele el nombre completo y el WhatsApp de la persona que va a inscribir y, en cuanto te los dé, "
        "llama a `registrar_datos_nuevo_socio`; recuérdale también mandar la captura de su comprobante."
        + ("" if sponsor_name else " Pregúntale también su nombre para presentárselo a su invitado.")
    )


async def register_prospect(http, sender_phone: str, nombre_completo: str = None, telefono_whatsapp: str = None,
                            nombre_patrocinador: str = None, **_ignored) -> str:
    enrollment = storage.get_open_enrollment_by_sponsor(sender_phone)
    if not enrollment:
        return "ERROR: no hay una inscripción en curso iniciada por este cliente; usa `iniciar_alta_patrocinada`."
    if enrollment.get("phone"):
        return (f"OK: ya tengo registrado a {enrollment.get('full_name')} ({enrollment['phone']}) para esta "
                f"inscripción. Si se equivocó de número, pídele que lo confirme y avísale que el equipo lo corrige.")
    name = re.sub(r"\s+", " ", nombre_completo or "").strip()
    if len(name.split(" ")) < 2:
        return "ERROR: necesito el nombre completo de la persona invitada (nombre y apellido)."
    try:
        phone = normalize_prospect_phone(telefono_whatsapp, sender_phone)
    except InvalidProspectPhone as exc:
        return f"ERROR: el WhatsApp del invitado no es válido: {exc}. Pídele que lo revise y te lo mande de nuevo."
    storage.set_enrollment_prospect(enrollment["id"], phone, name)
    if nombre_patrocinador and not enrollment.get("sponsor_name"):
        with storage._connection() as conn:
            conn.execute("UPDATE enrollments SET sponsor_name = ? WHERE id = ?", (nombre_patrocinador, enrollment["id"]))
    enrollment = storage.get_enrollment(enrollment["id"])
    if enrollment["flow_stage"] == "identidad_pendiente":
        sent = await send_ping(http, enrollment)
        return ("OK: datos registrados y como su pago ya está confirmado, ya le escribí a su invitado para pedirle "
                "su INE y su selfie." if sent else
                "OK: datos registrados, pero no pude escribirle a su invitado; ya le mandé instrucciones al cliente.")
    return ("OK: datos de su invitado registrados. En cuanto el equipo confirme su pago, le escribo a su invitado "
            "para validar su identidad. Si aún no manda su comprobante, recuérdaselo con amabilidad.")


async def start_direct(http, sender_phone: str, nombre_completo: str = None, tiene_patrocinador: bool = None,
                       id_patrocinador: str = None, direccion: str = None, **_ignored) -> str:
    if storage.get_member(sender_phone):
        return "OK: esta persona ya es socia activa de I'AM; no necesita inscribirse de nuevo."
    if storage.get_open_enrollment_for_prospect(sender_phone):
        return "OK: ya tiene su inscripción en curso; ayúdala a continuar desde donde se quedó."
    if tiene_patrocinador is None:
        return ("ERROR: antes de iniciar pregúntale si tiene un patrocinador (alguien que la invitó a I'AM) y vuelve "
                "a llamar esta función con `tiene_patrocinador`.")
    sponsor_id = (id_patrocinador or "").strip()
    if tiene_patrocinador and len(re.sub(r"\W", "", sponsor_id)) < 3:
        return "ERROR: dijo que tiene patrocinador: pídele el ID de empresario I'AM de su patrocinador."
    busy = _block_if_busy(sender_phone)
    if busy:
        return busy
    customer = storage.get_customer(sender_phone) or {}
    name = re.sub(r"\s+", " ", nombre_completo or customer.get("name") or "").strip() or None
    enrollment_id = storage.create_enrollment_flow("directo", prospect_phone=sender_phone, full_name=name)
    address = re.sub(r"\s+", " ", direccion or "").strip() or None
    sponsor_fields = {}
    if tiene_patrocinador:
        leader = storage.get_profile_by_empresario_id(sponsor_id) or {}
        sponsor_fields = {"sponsor_distributor_number": sponsor_id, "sponsor_name": leader.get("name"),
                          "sponsor_phone": leader.get("phone")}
    storage.set_enrollment_stage(enrollment_id, "pago_pendiente", address=address, **sponsor_fields)
    storage.create_enrollment_payment_order(sender_phone, enrollment_id, _product_label(), _money(_offer()["fee"]))
    logger.info("Alta directa %s iniciada por %s.", enrollment_id, sender_phone)
    await _say(http, sender_phone, payment_instructions(sponsored=False))
    missing = [x for x, ok in (("su nombre completo", name), (ADDRESS_REQUEST, address)) if not ok]
    return ("OK: inscripción iniciada y YA le envié el texto con la cuota, los beneficios y los datos bancarios. NO "
            "repitas cifras ni datos bancarios; solo acompáñalo con calidez y recuérdale mandar la captura de su "
            "comprobante." + (f" Pídele también {' y '.join(missing)} y regístralo con `registrar_datos_aspirante`."
                              if missing else ""))


async def register_applicant_data(http, sender_phone: str, direccion: str = None, nombre_completo: str = None,
                                  **_ignored) -> str:
    """El aspirante (directo o invitado) da su dirección o su nombre completo."""
    enrollment = storage.get_open_enrollment_for_prospect(sender_phone)
    if not enrollment:
        return "ERROR: esta persona no tiene un alta de socio en curso."
    address = re.sub(r"\s+", " ", direccion or "").strip()
    name = re.sub(r"\s+", " ", nombre_completo or "").strip()
    if address and len(address) < 12:
        return f"ERROR: la dirección se ve incompleta; pídele {ADDRESS_REQUEST}."
    if not address and not name:
        return "ERROR: no recibí ningún dato; pásame la dirección o el nombre completo que te dio."
    fields = {"address": address} if address else {}
    storage.set_enrollment_stage(enrollment["id"], enrollment["flow_stage"], **fields)
    if name and len(name.split(" ")) >= 2:
        with storage._connection() as conn:
            conn.execute("UPDATE enrollments SET full_name = ? WHERE id = ?", (name, enrollment["id"]))
    enrollment = storage.get_enrollment(enrollment["id"])
    if (enrollment["flow_stage"] == "identidad_pendiente" and enrollment.get("ine_media_id")
            and enrollment.get("face_media_id") and enrollment.get("address")):
        await _submit_for_review(http, enrollment)
        return ("OK: datos registrados y YA le avisé que su identidad está en revisión con el equipo. No lo repitas "
                "ni digas que está validado.")
    pending = []
    if not enrollment.get("address"):
        pending.append(ADDRESS_REQUEST)
    if enrollment["flow_stage"] == "identidad_pendiente":
        if not enrollment.get("ine_media_id"):
            pending.append("la foto de su INE por el frente")
        elif not enrollment.get("face_media_id"):
            pending.append("la selfie sosteniendo su INE")
    return "OK: datos registrados." + (f" Aún falta: {', '.join(pending)}." if pending else "")


# ---------------------------------------------------------------------------
# Eventos del pago (Control) y de la identidad (prospecto)
# ---------------------------------------------------------------------------

def on_proof_received(order: dict) -> None:
    enrollment = storage.get_enrollment_by_order(order["id"])
    if enrollment and enrollment["flow_stage"] == "pago_pendiente":
        storage.set_enrollment_stage(enrollment["id"], "pago_en_revision")


async def on_payment_approved(http, order: dict, approver_phone: str = None) -> str:
    enrollment = storage.get_enrollment_by_order(order["id"])
    if not enrollment:
        return "Pago de inscripción autorizado, pero no encontré el alta ligada; revísalo con Belén."
    storage.set_enrollment_stage(enrollment["id"], "identidad_pendiente", payment_approved_by=approver_phone)
    enrollment = storage.get_enrollment(enrollment["id"])
    payer = order["phone"]
    if enrollment["origin"] == "directo":
        await _say(http, payer, identity_request_text((enrollment.get("full_name") or "").split(" ")[0] or None,
                                                      needs_address=not enrollment.get("address")))
        return "Pago de inscripción autorizado. Ya le pedí su INE y su selfie al aspirante."
    if enrollment.get("phone"):
        await _say(http, payer, f"¡Tu pago de la inscripción ya fue confirmado! 🎉 Ya le escribí a "
                                f"{enrollment.get('full_name')} para validar su identidad; te aviso en cuanto quede activ@.")
        await send_ping(http, enrollment)
        return "Pago de inscripción autorizado. Ya contacté al invitado para su INE y selfie."
    await _say(http, payer, "¡Tu pago de la inscripción ya fue confirmado! 🎉 Para continuar, compárteme el nombre "
                            "completo y el número de WhatsApp de la persona que vas a inscribir.")
    return "Pago de inscripción autorizado. Le pedí al patrocinador los datos de su invitado."


async def on_payment_rejected(http, order: dict, motivo: str = None) -> None:
    enrollment = storage.get_enrollment_by_order(order["id"])
    if not enrollment:
        return
    storage.set_enrollment_stage(enrollment["id"], "pago_rechazado", rejection_reason=motivo)
    logger.info("Alta %s: pago rechazado (%s). El invitado no fue contactado.", enrollment["id"], motivo or "-")
    if enrollment["origin"] == "patrocinador":
        await _say(http, order["phone"], "La inscripción quedó en pausa hasta resolver tu pago; no contactamos a tu "
                                         "invitado. Cuando esté resuelto puedes iniciarla de nuevo conmigo.")


async def on_identity_photo(http, sender_phone: str, image_data: dict, enrollment: dict) -> None:
    media_id = (image_data or {}).get("id")
    if not media_id:
        return
    if not enrollment.get("ine_media_id"):
        # El número real con que escribe (Meta lo entrega como 521…) pasa a ser el de su alta.
        storage.set_enrollment_prospect(enrollment["id"], sender_phone, enrollment.get("full_name"))
        storage.set_enrollment_stage(enrollment["id"], "identidad_pendiente", ine_media_id=media_id)
        storage.log_message(sender_phone, "user", "[foto de INE]")
        await _say(http, sender_phone, "¡Gracias! Ya tengo tu identificación. Ahora mándame una *selfie sosteniendo "
                                       "tu INE al lado de tu rostro* (tomada en este momento).")
        return
    if enrollment.get("face_media_id"):
        await _say(http, sender_phone, "Ya tengo tus dos fotos 🙌" + (
            f" Solo me falta {ADDRESS_REQUEST}." if not enrollment.get("address") else ""))
        return
    storage.set_enrollment_stage(enrollment["id"], "identidad_pendiente", face_media_id=media_id)
    storage.log_message(sender_phone, "user", "[selfie con INE]")
    enrollment = storage.get_enrollment(enrollment["id"])
    if not enrollment.get("address"):
        await _say(http, sender_phone, f"¡Gracias, ya tengo tus fotos! 🙌 Para terminar, escríbeme {ADDRESS_REQUEST}.")
        return
    await _submit_for_review(http, enrollment)


async def _submit_for_review(http, enrollment: dict) -> None:
    sender_phone = enrollment["phone"]
    storage.set_enrollment_stage(enrollment["id"], "revision_identidad", status="enviado")
    enrollment = storage.get_enrollment(enrollment["id"])
    package = _review_package(enrollment)
    for reviewer in _cfg["reviewers"]:
        await _cfg["send_text"](http, reviewer, package)
        await _cfg["send_image"](http, reviewer, enrollment["ine_media_id"], "INE del aspirante (frente)")
        await _cfg["send_image"](http, reviewer, enrollment["face_media_id"], "Selfie del aspirante con su INE")
    if _cfg["log_review"]:
        _cfg["log_review"](package)
    logger.info("Alta %s enviada a revisión de identidad.", enrollment["id"])
    await _say(http, sender_phone, "¡Listo, ya tengo tus fotos! 🙌 Nuestro equipo está revisando tu identidad para "
                                   "activar tu cuenta. En cuanto termine te aviso por aquí.")


# ---------------------------------------------------------------------------
# Revisión de Belén / Yeni
# ---------------------------------------------------------------------------

async def review(http, telefono_aspirante: str = None, accion: str = None, motivo: str = None,
                 reviewer_phone: str = None, id_empresario: str = None, descuento_pct: float = None,
                 **_ignored) -> str:
    if accion not in REVIEW_ACTIONS:
        return f"ERROR: acción inválida; usa una de: {', '.join(REVIEW_ACTIONS)}."
    enrollment = storage.get_enrollment_for_review(re.sub(r"\D", "", telefono_aspirante or ""))
    if not enrollment:
        return f"ERROR: no hay un alta en revisión de identidad para el teléfono {telefono_aspirante}."
    phone, name = enrollment["phone"], enrollment.get("full_name") or "el aspirante"
    first = (enrollment.get("full_name") or "").split(" ")[0] or None

    if accion == "pedir_fotos":
        if not motivo:
            return "ERROR: falta el motivo: dime qué hay que corregir en las fotos para explicárselo al aspirante."
        storage.set_enrollment_stage(enrollment["id"], "identidad_pendiente", ine_media_id=None,
                                     face_media_id=None, status="recopilando")
        await _say(http, phone, f"{first + ', ' if first else ''}necesitamos que nos reenvíes tus fotos: {motivo}. "
                                f"Mándame de nuevo 1) tu INE por el frente y 2) una selfie sosteniéndola al lado de "
                                f"tu rostro.")
        return f"Listo, le pedí a {name} que reenvíe sus fotos."

    if accion == "rechazar":
        if not motivo:
            return "ERROR: necesito el motivo del rechazo para registrarlo."
        storage.set_enrollment_stage(enrollment["id"], "rechazado", rejection_reason=motivo, approved_by=reviewer_phone)
        await _say(http, phone, "Gracias por tu interés en I'AM. Por ahora no pudimos completar tu alta como socio; "
                                "una persona del equipo se va a poner en contacto contigo para explicarte.")
        if enrollment.get("sponsor_phone"):
            await _say(http, enrollment["sponsor_phone"], f"Te aviso que el alta de {name} no se pudo completar por "
                                                          f"ahora. El equipo de I'AM se comunicará contigo.")
        return (f"Alta de {name} rechazada ({motivo}). Ojo: su pago de {_money(_offer()['fee'])} ya estaba "
                f"confirmado; coordina con Ara el reembolso o la solución.")

    new_id = (id_empresario or "").strip()
    if len(re.sub(r"\W", "", new_id)) < 3 or descuento_pct is None:
        return ("ERROR: para aprobar necesito el ID de empresario que I'AM le asignó y el % de descuento que le "
                f"corresponde (normalmente {_offer()['discount_pct']}%). Pídeselos a quien aprueba.")
    pct = float(descuento_pct)
    if not 0 < pct <= MAX_MEMBER_DISCOUNT_PCT:
        return f"ERROR: el descuento debe ser mayor a 0 y máximo {MAX_MEMBER_DISCOUNT_PCT}%."
    taken = storage.get_profile_by_empresario_id(new_id)
    if taken and taken["phone"][-10:] != phone[-10:]:
        return (f"ERROR: el ID {new_id} ya está registrado a otra persona ({taken.get('name') or taken['phone']}). "
                f"Confírmalo antes de aprobar.")
    storage.activate_member(phone, enrollment["id"], pct, approved_by=reviewer_phone)
    sponsor_id = enrollment.get("sponsor_distributor_number")
    storage.save_verified_profile(
        phone, kind="socio", discount_pct=pct, empresario_id=new_id, name=enrollment.get("full_name"),
        scope="general", notes="Socio activado por TEO", verified_by=reviewer_phone,
        sponsor_empresario_id=sponsor_id, sponsor_phone=enrollment.get("sponsor_phone"),
        address=enrollment.get("address"),
    )
    if enrollment.get("sponsor_phone") and sponsor_id:
        storage.set_customer_empresario_id(enrollment["sponsor_phone"], sponsor_id)
    await _say(http, phone, welcome_text(first, pct, new_id))
    gift_note = ""
    if storage.get_active_order(phone):
        gift_note = " Tiene otro pedido en curso: su Combo Detox se le enviará cuando lo termine."
    else:
        storage.create_gift_order(phone, f"{pricing.ENROLLMENT_GIFT} (regalo de inscripción)", enrollment["id"])
    course = _cfg["begin_course"](phone) if _cfg["begin_course"] else None
    if course:
        await _say(http, phone, course)
    if enrollment.get("sponsor_phone"):
        await _say(http, enrollment["sponsor_phone"], sponsor_activated_text(name))
    logger.info("Alta %s ACTIVADA por %s (ID %s, %s%%).", enrollment["id"], reviewer_phone, new_id, pct)
    leader = storage.get_profile_by_empresario_id(sponsor_id) if sponsor_id else None
    commission_note = ""
    if sponsor_id and (not leader or leader.get("commission_pct") is None):
        commission_note = (f" Su líder (ID {sponsor_id}) aún no tiene % de comisión: dímelo cuando lo sepas para "
                           f"sus cortes (\"comisión de <líder> <%>\").")
    return (f"✅ {name} quedó activ@ como socio con ID {new_id} y {pct:g}% de descuento de por vida (ya guardado: se "
            f"le aplica solo en cada compra); bienvenida y día 1 del entrenamiento enviados"
            + (" y su patrocinador ya fue notificado" if enrollment.get("sponsor_phone") else "")
            + f". Le pedí su ubicación para cotizar el envío de su Combo Detox.{gift_note}{commission_note}")


# ---------------------------------------------------------------------------
# Contexto para TEO
# ---------------------------------------------------------------------------

def stage_notes(phone: str) -> list:
    """Notas de estado del alta para la conversación con este teléfono (prospecto, patrocinador o socio)."""
    notes = []
    as_prospect = storage.get_open_enrollment_for_prospect(phone)
    if as_prospect and as_prospect["flow_stage"] == "identidad_pendiente":
        missing = []
        if not as_prospect.get("ine_media_id"):
            missing.append("la foto de su INE por el frente")
        elif not as_prospect.get("face_media_id"):
            missing.append("la selfie sosteniendo su INE al lado de su rostro")
        if not as_prospect.get("address"):
            missing.append(ADDRESS_REQUEST + " (cuando la escriba, regístrala YA con `registrar_datos_aspirante`)")
        notes.append(f"Es un aspirante a socio con pago confirmado: falta {'; '.join(missing)}. Pídeselo con calidez; "
                     f"las fotos se procesan solas al recibirlas. No digas que ya está validado ni inscrito.")
    elif as_prospect and as_prospect["flow_stage"] == "revision_identidad":
        notes.append("Su identidad está en revisión por el equipo. No digas que está validado ni inscrito; dile que en "
                     "cuanto terminen le avisas.")
    elif as_prospect and as_prospect["flow_stage"] in ("pago_pendiente", "pago_en_revision") and \
            as_prospect["origin"] == "directo":
        notes.append("Inició su inscripción directa: " + ("falta su comprobante de pago." if
                     as_prospect["flow_stage"] == "pago_pendiente" else "su pago está en verificación; aún no pidas fotos."))
    sponsored = storage.get_open_enrollment_by_sponsor(phone)
    if sponsored:
        stage = sponsored["flow_stage"]
        missing = "" if sponsored.get("phone") else (
            " FALTAN el nombre completo y el WhatsApp de su invitado: si ya te los dio en la conversación, llama YA a "
            "`registrar_datos_nuevo_socio` con ellos; si no, pídeselos.")
        text = {
            "pago_pendiente": "Está inscribiendo a un nuevo socio: falta su comprobante de pago de la inscripción.",
            "pago_en_revision": "Está inscribiendo a un nuevo socio: su pago está en verificación por el equipo.",
            "identidad_pendiente": "Su pago de inscripción ya fue confirmado; esperamos la INE y selfie de su invitado.",
            "revision_identidad": "La identidad de su invitado está en revisión por el equipo.",
        }.get(stage, "")
        notes.append(text + missing)
    profile = storage.get_verified_profile(phone)
    if profile:
        who = "SOCIO ACTIVO de I'AM" if profile.get("profile_kind") == "socio" or profile.get("member_since") else \
            "DISTRIBUIDOR con ID ya verificado por Ventas"
        id_text = (f" Su ID de empresario es {profile['empresario_id']}: si te lo pregunta o lo olvidó, recuérdaselo."
                   if profile.get("empresario_id") else "")
        restricted = (" Su descuento tiene restricciones: el sistema lo manda solo a validar con Ventas usando su ID "
                      "guardado." if profile.get("discount_scope") == "restringido" else "")
        notes.append(f"Es {who}: tiene {profile['member_discount_pct']:g}% de descuento que el sistema aplica solo al "
                     f"cotizar; NUNCA le vuelvas a pedir su ID ni validación. Si quiere comprar, llama DE INMEDIATO a "
                     f"`iniciar_cotizacion` con el producto: ahí se le aplica su descuento y te da su precio."
                     f"{id_text}{restricted}")
    return notes


def pending_reviews_text() -> str:
    pending = storage.list_enrollments_for_review()
    if not pending:
        return ""
    return "ALTAS DE SOCIO EN REVISIÓN DE IDENTIDAD: " + "; ".join(
        f"{e.get('full_name') or 'sin nombre'} ({e['phone']}), patrocinador "
        f"{e.get('sponsor_name') or e.get('sponsor_distributor_number') or 'directo'}" for e in pending)
