"""Contabilidad de red y comisiones de líderes, con validación administrativa de Ara.

- Cada pedido pagado (autorizado por Control) de un socio o distribuidor con patrocinador registrado
  queda en `network_purchases`, ligado al ID de empresario de su líder. La base de la comisión es lo
  que pagó por los productos, ya con su descuento y sin envío.
- Cortes: días 14 y 29 de cada mes a las 11:00 (CDMX); en febrero, el segundo corte es el último día.
  Cada corte cubre lo autorizado desde el corte anterior.
- Comisión de cada líder = volumen del periodo × su % de comisión, que fija Belén por líder.
- Flujo: TEO manda a Control (Ara/Benjamín) el reporte preliminar con su Excel → Ara responde
  "correcto" o reporta un error → TEO le reenvía el Excel, ella lo corrige y lo regresa como archivo →
  TEO lo lee y pide confirmación → validado → Ara manda el comprobante de cada líder, uno por uno, y
  TEO entrega a cada líder su informe final con su comprobante.
- Ventana de 24 h de WhatsApp: si la persona no ha escrito en las últimas horas, se manda una
  plantilla y el contenido queda en espera hasta que responda (`on_inbound`).

Todas las cifras se calculan aquí, nunca en el modelo. main.py inyecta los envíos con `configure()`.
"""
import asyncio
import calendar
import io
import logging
import re
from datetime import datetime, timedelta, timezone

import pricing
import storage
from scheduler import TIMEZONE

logger = logging.getLogger("teo.comisiones")

CUT_DAYS = (14, 29)
CUT_HOUR = 11
WINDOW_HOURS = 23.5
MAX_COMMISSION_PCT = 100
TEMPLATE_AVISO = "teo_comisiones_revision"   # {{1}} periodo
TEMPLATE_LIDER = "teo_comisiones_lider"      # {{1}} nombre, {{2}} periodo, {{3}} monto
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SUMMARY_SHEET = "Resumen"
DETAIL_SHEET = "Detalle"
SUMMARY_HEADERS = ["#", "Líder", "ID empresario", "WhatsApp", "Compras", "Volumen (pagado sin envío)",
                   "% comisión", "Comisión"]
DETAIL_HEADERS = ["Fecha (CDMX)", "Socio", "ID socio", "WhatsApp socio", "Producto", "Precio público",
                  "% descuento", "Pagado sin envío", "ID líder"]

_cfg = {
    "send_text": None,       # async (http, to, text) -> None
    "send_document": None,   # async (http, to, data: bytes, filename, caption) -> bool
    "send_image": None,      # async (http, to, media_id, caption) -> None
    "send_template": None,   # async (http, to, template_name, params: list) -> bool
    "download": None,        # async (http, media_id) -> bytes
    "control_phones": (),    # Ara y Benjamín
    "sales_phone": None,     # Belén: fija el % de comisión de cada líder
    "remember": None,        # (phone, text) -> None
}


def configure(**kwargs) -> None:
    unknown = set(kwargs) - set(_cfg)
    if unknown:
        raise ValueError(f"Opciones desconocidas: {unknown}")
    _cfg.update(kwargs)


def money(value) -> str:
    return f"${float(value or 0):,.2f}"


def _parse_money(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(value))
    return float(match.group(0).replace(",", "")) if match else None


def _norm(empresario_id: str) -> str:
    return storage.normalize_empresario_id(empresario_id)


# ---------------------------------------------------------------------------
# Periodos de corte
# ---------------------------------------------------------------------------

def _cut_days_of(year: int, month: int) -> list:
    last = calendar.monthrange(year, month)[1]
    return sorted({min(day, last) for day in CUT_DAYS})


def is_cut_moment(now_local: datetime) -> bool:
    return now_local.day in _cut_days_of(now_local.year, now_local.month) and now_local.hour >= CUT_HOUR


def previous_cut(cut_local: datetime) -> datetime:
    days = _cut_days_of(cut_local.year, cut_local.month)
    earlier = [d for d in days if d < cut_local.day]
    if earlier:
        return cut_local.replace(day=earlier[-1])
    first_of_month = cut_local.replace(day=1)
    prev_month = first_of_month - timedelta(days=1)
    return cut_local.replace(year=prev_month.year, month=prev_month.month,
                             day=_cut_days_of(prev_month.year, prev_month.month)[-1])


def period_for_cut(cut_local: datetime) -> dict:
    cut_local = cut_local.replace(hour=CUT_HOUR, minute=0, second=0, microsecond=0)
    start_local = previous_cut(cut_local)

    def to_utc(dt):
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    label = f"{start_local.strftime('%d/%m/%Y')} {CUT_HOUR}:00 al {cut_local.strftime('%d/%m/%Y')} {CUT_HOUR}:00"
    return {"key": cut_local.date().isoformat(), "start_utc": to_utc(start_local), "end_utc": to_utc(cut_local),
            "label": label}


def _local_date(utc_text: str) -> str:
    try:
        dt = datetime.strptime(utc_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone(TIMEZONE).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return utc_text or ""


# ---------------------------------------------------------------------------
# Registro de compras de la red
# ---------------------------------------------------------------------------

def record_purchase(order: dict) -> bool:
    """Registra un pedido pagado si el comprador es socio/distribuidor con líder conocido."""
    if (order.get("kind") or "pedido") != "pedido":
        return False
    profile = storage.get_verified_profile(order["phone"])
    if not profile or not profile.get("sponsor_empresario_id"):
        return False
    paid = _parse_money(order.get("amount_expected"))
    if not paid:
        return False
    public = _parse_money(order.get("amount_public")) or paid
    saved = storage.record_network_purchase(
        order["id"], order["phone"], profile.get("name"), profile.get("empresario_id"),
        profile["sponsor_empresario_id"], order.get("product"), public,
        order.get("discount_pct"), round(paid, 2),
    )
    if saved:
        logger.info("Compra de red registrada: orden %s de %s (líder %s, %s).", order["id"], order["phone"],
                    profile["sponsor_empresario_id"], money(paid))
    return saved


def compute_lines(purchases: list) -> list:
    groups = {}
    for p in purchases:
        key = _norm(p["leader_empresario_id"])
        group = groups.setdefault(key, {"leader_empresario_id": p["leader_empresario_id"], "purchases": 0, "volume": 0.0})
        group["purchases"] += 1
        group["volume"] = round(group["volume"] + float(p["amount_paid"] or 0), 2)
    lines = []
    for group in groups.values():
        leader = storage.get_profile_by_empresario_id(group["leader_empresario_id"]) or {}
        pct = leader.get("commission_pct")
        group.update({
            "leader_phone": leader.get("phone"),
            "leader_name": leader.get("name"),
            "commission_pct": pct,
            "commission": pricing.commission_amount(group["volume"], pct) if pct is not None else None,
        })
        lines.append(group)
    lines.sort(key=lambda g: (-g["volume"], g["leader_name"] or ""))
    return lines


def _purchases_of(run: dict, leader_empresario_id: str = None) -> list:
    rows = storage.list_network_purchases(run["period_start_utc"], run["period_end_utc"])
    if leader_empresario_id:
        rows = [r for r in rows if _norm(r["leader_empresario_id"]) == _norm(leader_empresario_id)]
    return rows


# ---------------------------------------------------------------------------
# Excel (envío a Ara y lectura de su versión corregida)
# ---------------------------------------------------------------------------

def build_xlsx(run: dict, lines: list) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = SUMMARY_SHEET
    ws.append([f"Comisiones I'AM — periodo {run['label']}"])
    ws["A1"].font = Font(bold=True)
    ws.append(SUMMARY_HEADERS)
    for cell in ws[2]:
        cell.font = Font(bold=True)
    for line in lines:
        ws.append([line["position"], line.get("leader_name") or "", line["leader_empresario_id"],
                   line.get("leader_phone") or "", line["purchases"], line["volume"],
                   line.get("commission_pct"), line.get("commission")])
    total = sum(float(l.get("commission") or 0) for l in lines)
    ws.append(["", "TOTAL", "", "", sum(l["purchases"] for l in lines),
               round(sum(l["volume"] for l in lines), 2), "", round(total, 2)])
    ws.append([])
    ws.append(["Para corregir: cambia solo las columnas Volumen, % comisión o Comisión (si dejas Comisión vacía "
               "se calcula con el %). No cambies el ID empresario. Guarda y reenvía este archivo a TEO."])
    for column, width in zip("ABCDEFGH", (4, 28, 16, 16, 9, 24, 12, 14)):
        ws.column_dimensions[column].width = width

    detail = wb.create_sheet(DETAIL_SHEET)
    detail.append(DETAIL_HEADERS)
    for cell in detail[1]:
        cell.font = Font(bold=True)
    for p in _purchases_of(run):
        detail.append([_local_date(p["created_at"]), p.get("buyer_name") or "", p.get("buyer_empresario_id") or "",
                       p["buyer_phone"], p.get("product") or "", p.get("amount_public"), p.get("discount_pct"),
                       p["amount_paid"], p["leader_empresario_id"]])
    for column, width in zip("ABCDEFGHI", (13, 26, 16, 16, 32, 14, 12, 16, 16)):
        detail.column_dimensions[column].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


class InvalidReportFile(ValueError):
    pass


def parse_xlsx(data: bytes) -> list:
    """Filas del Resumen corregido: [{id, volume, pct, commission}]. Lanza InvalidReportFile."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), data_only=True)
    except Exception as exc:
        raise InvalidReportFile("no pude abrir el archivo; mándalo en formato Excel (.xlsx)") from exc
    if SUMMARY_SHEET not in wb.sheetnames:
        raise InvalidReportFile(f"el archivo no tiene la hoja '{SUMMARY_SHEET}'; usa el Excel que te mandé")
    ws = wb[SUMMARY_SHEET]
    header_row = None
    for row in ws.iter_rows(min_row=1, max_row=6):
        values = [str(c.value or "").strip().lower() for c in row]
        if "id empresario" in values:
            header_row = row[0].row
            columns = {name: values.index(name.lower()) for name in SUMMARY_HEADERS if name.lower() in values}
            break
    if not header_row:
        raise InvalidReportFile("no encontré los encabezados del Resumen; usa el Excel que te mandé")
    rows = []
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        empresario_id = str(row[columns["ID empresario"]] or "").strip()
        if not empresario_id:
            continue

        def cell(name):
            index = columns.get(name)
            return row[index] if index is not None and index < len(row) else None

        rows.append({
            "id": empresario_id,
            "volume": _parse_money(cell("Volumen (pagado sin envío)")),
            "pct": _parse_money(cell("% comisión")),
            "commission": _parse_money(cell("Comisión")),
        })
    return rows


def apply_corrections(run_id: int, rows: list) -> dict:
    lines = {(_norm(l["leader_empresario_id"])): l for l in storage.list_commission_lines(run_id)}
    changes, unknown = [], []
    for row in rows:
        line = lines.get(_norm(row["id"]))
        if not line:
            unknown.append(row["id"])
            continue
        volume = row["volume"] if row["volume"] is not None else line["volume"]
        pct = row["pct"] if row["pct"] is not None else line.get("commission_pct")
        commission = row["commission"]
        if commission is None and pct is not None:
            commission = pricing.commission_amount(volume, pct)
        updates = {}
        if round(volume, 2) != round(line["volume"], 2):
            updates["volume"] = round(volume, 2)
        if pct != line.get("commission_pct"):
            updates["commission_pct"] = pct
        if commission is not None and (line.get("commission") is None or round(commission, 2) != round(line["commission"], 2)):
            updates["commission"] = round(commission, 2)
        if updates:
            storage.update_commission_line(line["id"], adjusted=1, **updates)
            before = money(line["commission"]) if line.get("commission") is not None else "sin calcular"
            after = money(updates.get("commission", line.get("commission")))
            changes.append(f"{line.get('leader_name') or line['leader_empresario_id']}: {before} → {after}")
    return {"changes": changes, "unknown": unknown}


# ---------------------------------------------------------------------------
# Textos
# ---------------------------------------------------------------------------

def _line_text(line: dict) -> str:
    who = f"{line.get('leader_name') or 'Líder sin nombre'} (ID {line['leader_empresario_id']})"
    base = f"{who}: {line['purchases']} compra(s) · volumen {money(line['volume'])}"
    if line.get("commission") is None:
        return base + " · ⚠️ falta su % de comisión"
    pct = f"{line['commission_pct']:g}% → " if line.get("commission_pct") is not None else ""
    return base + f" · {pct}*{money(line['commission'])}*" + (" (ajustado)" if line.get("adjusted") else "")


def control_summary(run: dict, lines: list) -> str:
    total = sum(float(l.get("commission") or 0) for l in lines)
    missing = [l for l in lines if l.get("commission") is None]
    text = (f"📊 *Reporte preliminar de comisiones*\nPeriodo: {run['label']}\n\n"
            + "\n".join(f"{l['position']}. {_line_text(l)}" for l in lines)
            + f"\n\nTotal a pagar: *{money(total)}*")
    if missing:
        text += ("\n\n⚠️ Hay líderes sin % de comisión: Belén lo fija, o puedes capturar la comisión directamente "
                 "en el Excel.")
    return text + ("\n\nTe adjunto el Excel con el detalle de cada compra. Si todo está correcto, respóndeme "
                   "\"correcto\". Si hay algún error, dime cuál o corrige el Excel y reenvíamelo como archivo.")


def leader_report(run: dict, line: dict) -> str:
    purchases = _purchases_of(run, line["leader_empresario_id"])
    first = (line.get("leader_name") or "").split(" ")[0]
    detail = "\n".join(
        f"• {_local_date(p['created_at'])} {p.get('buyer_name') or p['buyer_phone']} — "
        f"{p.get('product') or 'pedido'}: {money(p['amount_paid'])}" for p in purchases
    ) or "• (detalle registrado por Administración)"
    pct = ""
    if line.get("commission_pct") is not None and \
            abs(pricing.commission_amount(line["volume"], line["commission_pct"]) - float(line["commission"] or 0)) < 0.01:
        pct = f" ({line['commission_pct']:g}%)"
    return (
        f"📊 *Tu informe de comisiones I'AM*{', ' + first if first else ''}\n"
        f"Periodo: {run['label']}\n"
        f"ID de empresario: {line['leader_empresario_id']}\n\n"
        f"Compras de tu red directa:\n{detail}\n\n"
        f"Volumen de tu red: {money(line['volume'])}\n"
        f"Tu comisión{pct}: *{money(line['commission'])}*\n"
        + ("Monto validado y ajustado por Administración.\n" if line.get("adjusted") else "Monto validado por Administración.\n")
        + "\nTe comparto el comprobante de tu pago 👇 ¡Gracias por hacer crecer tu red!"
    )


# ---------------------------------------------------------------------------
# Entregas (con ventana de 24 h)
# ---------------------------------------------------------------------------

def _in_window(phone: str) -> bool:
    hours = storage.hours_since_inbound(phone)
    return hours is not None and hours <= WINDOW_HOURS


async def _say(http, phone: str, text: str) -> None:
    await _cfg["send_text"](http, phone, text)
    if _cfg["remember"]:
        _cfg["remember"](phone, text)


async def _send_preliminary_now(http, phone: str, run: dict) -> None:
    lines = storage.list_commission_lines(run["id"])
    await _say(http, phone, control_summary(run, lines))
    await _cfg["send_document"](http, phone, build_xlsx(run, lines), f"comisiones_{run['period_key']}.xlsx",
                                f"Comisiones {run['label']}")


async def _deliver_or_queue(http, phone: str, kind: str, ref_id: int, send_now, template: str, params: list) -> str:
    if _in_window(phone):
        await send_now()
        return "entregado"
    storage.queue_delivery(phone, kind, ref_id)
    sent = await _cfg["send_template"](http, phone, template, params)
    logger.info("%s para %s en espera de respuesta (plantilla %s: %s).", kind, phone, template, "ok" if sent else "falló")
    return "en_espera"


async def send_preliminary(http, run: dict) -> None:
    for phone in _cfg["control_phones"]:
        await _deliver_or_queue(http, phone, "reporte_comisiones", run["id"],
                                lambda p=phone: _send_preliminary_now(http, p, run), TEMPLATE_AVISO, [run["label"]])


def _missing_pct_text(run: dict, lines: list) -> str:
    missing = [l for l in lines if l.get("commission") is None]
    return (f"📊 Para el corte de comisiones del {run['label']} falta el % de comisión de: "
            + "; ".join(f"{l.get('leader_name') or 'sin nombre'} (ID {l['leader_empresario_id']})" for l in missing)
            + ". Dime, por ejemplo, \"comisión de Laura 10%\" y actualizo el reporte de Ara.")


async def _notify_missing_pct(http, run: dict, lines: list) -> None:
    phone = _cfg["sales_phone"]
    if not phone or not any(l.get("commission") is None for l in lines):
        return
    await _deliver_or_queue(http, phone, "falta_comision", run["id"],
                            lambda: _say(http, phone, _missing_pct_text(run, storage.list_commission_lines(run["id"]))),
                            TEMPLATE_AVISO, [run["label"]])


async def _deliver_leader_now(http, run: dict, line: dict) -> None:
    await _say(http, line["leader_phone"], leader_report(run, line))
    if line.get("proof_media_id"):
        await _cfg["send_image"](http, line["leader_phone"], line["proof_media_id"], "Comprobante de pago de tu comisión")
    storage.update_commission_line(line["id"], delivery_status="entregado")


async def deliver_to_leader(http, run: dict, line: dict) -> str:
    if not line.get("leader_phone"):
        storage.update_commission_line(line["id"], delivery_status="sin_telefono")
        return "sin_telefono"
    first = (line.get("leader_name") or "").split(" ")[0] or "líder"
    result = await _deliver_or_queue(
        http, line["leader_phone"], "comision_lider", line["id"], lambda: _deliver_leader_now(http, run, line),
        TEMPLATE_LIDER, [first, run["label"], money(line["commission"])],
    )
    if result == "en_espera":
        storage.update_commission_line(line["id"], delivery_status="esperando_respuesta")
    return result


async def on_inbound(http, phone: str) -> None:
    """Cada mensaje entrante: registra la actividad y entrega lo que estaba en espera."""
    storage.touch_inbound(phone)
    for item in storage.take_pending_deliveries(phone):
        try:
            if item["kind"] == "reporte_comisiones":
                run = storage.get_commission_run(item["ref_id"])
                if run and run["status"] in ("revision_control", "correccion"):
                    await _send_preliminary_now(http, phone, run)
            elif item["kind"] == "falta_comision":
                run = storage.get_commission_run(item["ref_id"])
                lines = storage.list_commission_lines(item["ref_id"]) if run else []
                if run and any(l.get("commission") is None for l in lines):
                    await _say(http, phone, _missing_pct_text(run, lines))
            elif item["kind"] == "comision_lider":
                line = storage.get_commission_line(item["ref_id"])
                if line and line["delivery_status"] != "entregado":
                    await _deliver_leader_now(http, storage.get_commission_run(line["run_id"]), line)
        except Exception:
            logger.exception("No se pudo entregar %s %s a %s.", item["kind"], item["ref_id"], phone)


# ---------------------------------------------------------------------------
# Corte
# ---------------------------------------------------------------------------

async def run_cut(http, cut_local: datetime):
    period = period_for_cut(cut_local)
    purchases = storage.list_network_purchases(period["start_utc"], period["end_utc"])
    lines = compute_lines(purchases)
    run_id = storage.create_commission_run(period["key"], period["start_utc"], period["end_utc"], period["label"], lines)
    if run_id is None:
        return None
    run = storage.get_commission_run(run_id)
    if not lines:
        storage.set_commission_run_status(run_id, "entregado", notes="sin compras de red en el periodo")
        logger.info("Corte de comisiones %s: sin compras de red.", period["key"])
        return run_id
    logger.info("Corte de comisiones %s: %s líder(es), %s compra(s).", period["key"], len(lines), len(purchases))
    await send_preliminary(http, run)
    await _notify_missing_pct(http, run, storage.list_commission_lines(run_id))
    return run_id


async def run_commission_loop(http, check_seconds: int = 60) -> None:
    logger.info("Loop de cortes de comisiones iniciado (días %s a las %s:00).", "/".join(map(str, CUT_DAYS)), CUT_HOUR)
    while True:
        try:
            now = datetime.now(TIMEZONE)
            if is_cut_moment(now):
                await run_cut(http, now)
        except Exception:
            logger.exception("Error en el corte de comisiones.")
        await asyncio.sleep(check_seconds)


# ---------------------------------------------------------------------------
# Herramientas de Control (Ara) y de Belén
# ---------------------------------------------------------------------------

def _open_run_or_error():
    run = storage.get_open_commission_run()
    if not run:
        return None, "No hay ningún reporte de comisiones abierto en este momento."
    return run, None


def _next_pending_proof(run_id: int):
    for line in storage.list_commission_lines(run_id):
        if line["delivery_status"] == "pendiente" and (line.get("commission") or 0) > 0:
            return line
    return None


def _next_proof_request(run_id: int) -> str:
    line = _next_pending_proof(run_id)
    if not line:
        return ""
    return (f"Mándame el comprobante de *{line.get('leader_name') or line['leader_empresario_id']}* "
            f"(ID {line['leader_empresario_id']}, {money(line['commission'])}).")


async def view_report(http, **_ignored) -> str:
    run, error = _open_run_or_error()
    if error:
        return error
    lines = storage.list_commission_lines(run["id"])
    status = {"revision_control": "en tu revisión", "correccion": "esperando tu Excel corregido",
              "validado": "validado; faltan comprobantes"}.get(run["status"], run["status"])
    text = control_summary(run, lines).split("\n\nTe adjunto")[0] + f"\n\nEstado: {status}."
    if run["status"] == "validado":
        text += " " + _next_proof_request(run["id"])
    return text


async def validate_report(http, staff_phone: str = None, **_ignored) -> str:
    run, error = _open_run_or_error()
    if error:
        return error
    if run["status"] == "validado":
        return "Ese reporte ya está validado. " + _next_proof_request(run["id"])
    lines = storage.list_commission_lines(run["id"])
    missing = [l for l in lines if l.get("commission") is None]
    if missing:
        return ("Todavía no lo puedo validar: falta la comisión de "
                + ", ".join(l.get("leader_name") or l["leader_empresario_id"] for l in missing)
                + ". Pídele a Belén su % o captúrala en el Excel y reenvíamelo.")
    storage.set_commission_run_status(run["id"], "validado", validated_by=staff_phone)
    for line in lines:
        if not (line.get("commission") or 0) > 0:
            storage.update_commission_line(line["id"], delivery_status="sin_comision")
    logger.info("Reporte de comisiones %s validado por %s.", run["period_key"], staff_phone)
    if not _next_pending_proof(run["id"]):
        storage.set_commission_run_status(run["id"], "entregado")
        return "✅ Reporte validado. No hay comisiones por pagar en este periodo."
    total = sum(float(l.get("commission") or 0) for l in lines)
    return (f"✅ Reporte validado (total {money(total)}). Ahora mándame aquí la foto del comprobante de pago de "
            f"cada líder, uno por uno, y yo le entrego a cada quien su informe final con su comprobante. "
            + _next_proof_request(run["id"]))


async def report_error(http, staff_phone: str = None, detalle: str = None, **_ignored) -> str:
    run, error = _open_run_or_error()
    if error:
        return error
    if run["status"] == "validado":
        return "Ese reporte ya fue validado y se están entregando los comprobantes; ya no se puede corregir aquí."
    storage.set_commission_run_status(run["id"], "correccion", notes=detalle)
    lines = storage.list_commission_lines(run["id"])
    await _cfg["send_document"](http, staff_phone, build_xlsx(run, lines), f"comisiones_{run['period_key']}.xlsx",
                                "Corrige este Excel y reenvíamelo")
    return ("Anotado" + (f" ({detalle})" if detalle else "") + ". Te mandé el Excel editable: corrige los montos en "
            "la hoja Resumen (Volumen, % comisión o Comisión), guárdalo y reenvíamelo aquí como archivo.")


async def on_control_document(http, phone: str, document: dict) -> bool:
    """Excel corregido de Ara. Devuelve False si no hay reporte esperando corrección."""
    run = storage.get_open_commission_run()
    if not run or run["status"] not in ("revision_control", "correccion"):
        return False
    name = (document or {}).get("filename") or ""
    if name and not name.lower().endswith((".xlsx", ".xlsm")):
        await _say(http, phone, "Para corregir el reporte necesito el archivo de Excel (.xlsx) que te mandé.")
        return True
    try:
        data = await _cfg["download"](http, document.get("id"))
        result = apply_corrections(run["id"], parse_xlsx(data))
    except InvalidReportFile as exc:
        await _say(http, phone, f"No pude leer tu archivo: {exc}.")
        return True
    storage.set_commission_run_status(run["id"], "revision_control")
    lines = storage.list_commission_lines(run["id"])
    changes = ("Cambios que tomé:\n" + "\n".join(f"• {c}" for c in result["changes"])) if result["changes"] else \
        "No encontré cambios respecto al reporte."
    unknown = (f"\n\n⚠️ Estos ID no están en el corte y los ignoré: {', '.join(result['unknown'])}."
               if result["unknown"] else "")
    total = sum(float(l.get("commission") or 0) for l in lines)
    await _say(http, phone, f"📥 Recibí tu Excel. {changes}{unknown}\n\nNuevo total a pagar: *{money(total)}*. "
                            f"Si así queda, respóndeme \"correcto\" para validarlo.")
    logger.info("Excel corregido de comisiones %s recibido de %s: %s cambio(s).", run["period_key"], phone,
                len(result["changes"]))
    return True


async def on_control_image(http, phone: str, image: dict) -> bool:
    """Comprobante de pago de la comisión del siguiente líder pendiente."""
    run = storage.get_open_commission_run()
    if not run or run["status"] != "validado":
        return False
    line = _next_pending_proof(run["id"])
    if not line:
        return False
    storage.update_commission_line(line["id"], proof_media_id=(image or {}).get("id"))
    line = storage.get_commission_line(line["id"])
    result = await deliver_to_leader(http, run, line)
    who = line.get("leader_name") or line["leader_empresario_id"]
    status = {
        "entregado": f"✅ Comprobante de {who} registrado y ya le entregué su informe final.",
        "en_espera": (f"✅ Comprobante de {who} registrado. Como no me ha escrito en las últimas 24 h, le mandé un "
                      f"aviso y le entrego su informe en cuanto me responda."),
        "sin_telefono": (f"✅ Comprobante de {who} registrado, pero no tengo su WhatsApp: entrégale tú su informe "
                         f"(ID {line['leader_empresario_id']}, {money(line['commission'])})."),
    }[result]
    following = _next_proof_request(run["id"])
    if not following:
        storage.set_commission_run_status(run["id"], "entregado")
        following = "Con este quedan registrados todos los comprobantes del periodo. 🙌"
    await _say(http, phone, f"{status}\n\n{following}")
    return True


async def set_leader_commission(http, lider: str = None, porcentaje: float = None, telefono_lider: str = None,
                                nombre_lider: str = None, **_ignored) -> str:
    """Belén fija el % de comisión de un líder (por ID de empresario o teléfono)."""
    if porcentaje is None or not 0 < float(porcentaje) <= MAX_COMMISSION_PCT:
        return "ERROR: necesito el porcentaje de comisión del líder (mayor a 0)."
    pct = float(porcentaje)
    leader = storage.find_leader(lider or "") or (storage.find_leader(telefono_lider) if telefono_lider else None)
    if not leader:
        digits = re.sub(r"\D", "", telefono_lider or "")
        if len(digits) < 10 or not lider:
            return (f"ERROR: no tengo registrado al líder {lider}. Dame su ID de empresario, su WhatsApp y su nombre "
                    f"para darlo de alta en la red.")
        phone = "52" + digits[-10:]
        storage.upsert_customer(phone, name=nombre_lider or None)
        storage.set_customer_empresario_id(phone, lider)
        leader = storage.get_customer(phone)
    storage.set_commission_pct(leader["phone"], pct)
    updated = ""
    run = storage.get_open_commission_run()
    if run and run["status"] in ("revision_control", "correccion"):
        for line in storage.list_commission_lines(run["id"]):
            if _norm(line["leader_empresario_id"]) == _norm(leader.get("empresario_id")) and not line.get("adjusted"):
                commission = pricing.commission_amount(line["volume"], pct)
                storage.update_commission_line(line["id"], commission_pct=pct, commission=commission,
                                               leader_phone=leader["phone"], leader_name=leader.get("name"))
                updated = f" Actualicé el reporte abierto: su comisión del periodo queda en {money(commission)}."
                for phone in _cfg["control_phones"]:
                    if _in_window(phone):
                        await _say(http, phone, f"ℹ️ Belén fijó el {pct:g}% de comisión para "
                                                f"{leader.get('name') or leader['empresario_id']}: "
                                                f"{money(commission)}. Escribe \"ver reporte\" para revisarlo.")
    logger.info("Comisión de %s fijada en %s%%.", leader.get("empresario_id"), pct)
    return (f"OK: {leader.get('name') or 'el líder'} (ID {leader.get('empresario_id')}) queda con {pct:g}% de "
            f"comisión sobre las compras de su red directa.{updated}")


def missing_pct_note() -> str:
    """Para el contexto de Belén: líderes con red y sin % de comisión."""
    run = storage.get_open_commission_run()
    if not run:
        return ""
    missing = [l for l in storage.list_commission_lines(run["id"]) if l.get("commission") is None]
    if not missing:
        return ""
    return "LÍDERES SIN % DE COMISIÓN EN EL CORTE ABIERTO (pregúntaselo a Belén y usa `fijar_comision_lider`): " + "; ".join(
        f"{l.get('leader_name') or 'sin nombre'} (ID {l['leader_empresario_id']})" for l in missing)
