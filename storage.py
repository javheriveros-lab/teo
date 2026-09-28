import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "teo.db"

REMINDER_KINDS = (
    "checkin_matutino",
    "recordatorio_dosis",
    "educacion_receta",
    "checkin_nocturno",
    "resumen_semanal",
)


@contextmanager
def _connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    """Migración aditiva: agrega `column` a `table` solo si no existe ya, sin tocar filas
    existentes. Usado para evolucionar el esquema sin recrear la base de datos en producción."""
    existing_columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing_columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    with _connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                phone TEXT PRIMARY KEY,
                name TEXT,
                age TEXT,
                health_notes TEXT,
                protocol TEXT,
                paused INTEGER NOT NULL DEFAULT 0,
                share_status TEXT,
                referral_code TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _ensure_column(conn, "customers", "distributor_mode", "distributor_mode INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN (
                    'checkin_matutino', 'recordatorio_dosis', 'educacion_receta',
                    'checkin_nocturno', 'resumen_semanal'
                )),
                hour INTEGER NOT NULL,
                minute INTEGER NOT NULL,
                meal_label TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                last_sent_date TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                date TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('matutino', 'dosis', 'nocturno')),
                responded INTEGER NOT NULL DEFAULT 0,
                energy_score INTEGER,
                note TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_content (
                phone TEXT NOT NULL,
                day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
                food_suggestion TEXT,
                training_focus TEXT,
                PRIMARY KEY (phone, day_of_week)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_phone ON conversations (phone, id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                product TEXT,
                amount_expected TEXT,
                dest_lat REAL,
                dest_lng REAL,
                dest_location_name TEXT,
                delivery_notes TEXT,
                total_weight_kg REAL,
                distance_km REAL,
                shipping_options_json TEXT,
                shipping_choice TEXT,
                shipping_cost TEXT,
                proof_media_id TEXT,
                vision_summary TEXT,
                status TEXT NOT NULL DEFAULT 'cotizando' CHECK (status IN (
                    'cotizando', 'cotizacion_pendiente_aprobacion', 'cotizado',
                    'pendiente_pago', 'en_revision', 'autorizado', 'rechazado'
                )),
                delivery_estimate TEXT,
                rejection_reason TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_phone ON orders (phone, id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS enrollments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                sponsor_distributor_number TEXT,
                sponsor_name TEXT,
                full_name TEXT,
                age TEXT,
                ine_media_id TEXT,
                face_media_id TEXT,
                reflection_time TEXT,
                status TEXT NOT NULL DEFAULT 'recopilando' CHECK (status IN (
                    'recopilando', 'completo', 'enviado', 'confirmado'
                )),
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_enrollments_phone ON enrollments (phone, id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                product TEXT PRIMARY KEY,
                quantity INTEGER NOT NULL DEFAULT 0,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS production_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                warranty_band TEXT,
                reported_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def upsert_customer(
    phone: str,
    name: str = None,
    age: str = None,
    health_notes: str = None,
    protocol: str = None,
    share_status: str = None,
    referral_code: str = None,
) -> None:
    with _connection() as conn:
        existing = conn.execute("SELECT phone FROM customers WHERE phone = ?", (phone,)).fetchone()
        if existing:
            fields, values = [], []
            if name is not None:
                fields.append("name = ?")
                values.append(name)
            if age is not None:
                fields.append("age = ?")
                values.append(age)
            if health_notes is not None:
                fields.append("health_notes = ?")
                values.append(health_notes)
            if protocol is not None:
                fields.append("protocol = ?")
                values.append(protocol)
            if share_status is not None:
                fields.append("share_status = ?")
                values.append(share_status)
            if referral_code is not None:
                fields.append("referral_code = ?")
                values.append(referral_code)
            if not fields:
                return
            fields.append("updated_at = CURRENT_TIMESTAMP")
            values.append(phone)
            conn.execute(f"UPDATE customers SET {', '.join(fields)} WHERE phone = ?", values)
        else:
            conn.execute(
                "INSERT INTO customers (phone, name, age, health_notes, protocol, share_status, referral_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (phone, name, age, health_notes, protocol, share_status, referral_code),
            )


def get_customer(phone: str):
    with _connection() as conn:
        row = conn.execute("SELECT * FROM customers WHERE phone = ?", (phone,)).fetchone()
        return dict(row) if row else None


def set_paused(phone: str, paused: bool) -> None:
    with _connection() as conn:
        existing = conn.execute("SELECT phone FROM customers WHERE phone = ?", (phone,)).fetchone()
        if existing:
            conn.execute("UPDATE customers SET paused = ? WHERE phone = ?", (int(paused), phone))
        else:
            conn.execute("INSERT INTO customers (phone, paused) VALUES (?, ?)", (phone, int(paused)))


def set_distributor_mode(phone: str, enabled: bool) -> None:
    """Activa/desactiva el modo distribuidor para un número de staff — les permite usar el
    mismo flujo conversacional que un distribuidor común (plan de optimización, catálogo, etc.)
    en lugar de sus comandos internos."""
    with _connection() as conn:
        existing = conn.execute("SELECT phone FROM customers WHERE phone = ?", (phone,)).fetchone()
        if existing:
            conn.execute("UPDATE customers SET distributor_mode = ? WHERE phone = ?", (int(enabled), phone))
        else:
            conn.execute("INSERT INTO customers (phone, distributor_mode) VALUES (?, ?)", (phone, int(enabled)))


def is_distributor_mode(phone: str) -> bool:
    with _connection() as conn:
        row = conn.execute("SELECT distributor_mode FROM customers WHERE phone = ?", (phone,)).fetchone()
        return bool(row["distributor_mode"]) if row else False


def set_reminders(phone: str, reminders: list) -> None:
    """reminders: lista de (kind, hour, minute, meal_label_o_None). Reemplaza los previos del cliente."""
    with _connection() as conn:
        conn.execute("DELETE FROM reminders WHERE phone = ?", (phone,))
        conn.executemany(
            "INSERT INTO reminders (phone, kind, hour, minute, meal_label) VALUES (?, ?, ?, ?, ?)",
            [(phone, kind, hour, minute, meal_label) for kind, hour, minute, meal_label in reminders],
        )


def set_daily_content(phone: str, rows: list) -> None:
    """rows: lista de (day_of_week, food_suggestion, training_focus). Reemplaza el plan semanal previo."""
    with _connection() as conn:
        conn.execute("DELETE FROM daily_content WHERE phone = ?", (phone,))
        conn.executemany(
            "INSERT INTO daily_content (phone, day_of_week, food_suggestion, training_focus) VALUES (?, ?, ?, ?)",
            [(phone, day, food, training) for day, food, training in rows],
        )


def get_daily_content(phone: str, day_of_week: int):
    with _connection() as conn:
        row = conn.execute(
            "SELECT food_suggestion, training_focus FROM daily_content WHERE phone = ? AND day_of_week = ?",
            (phone, day_of_week),
        ).fetchone()
        return dict(row) if row else None


def get_due_reminders(hour: int, minute: int, today_str: str) -> list:
    with _connection() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.phone, r.kind, r.meal_label FROM reminders r
            LEFT JOIN customers c ON c.phone = r.phone
            WHERE r.active = 1 AND r.hour = ? AND r.minute = ?
              AND (r.last_sent_date IS NULL OR r.last_sent_date != ?)
              AND COALESCE(c.paused, 0) = 0
            """,
            (hour, minute, today_str),
        ).fetchall()
        return [dict(row) for row in rows]


def mark_reminder_sent(reminder_id: int, today_str: str) -> None:
    with _connection() as conn:
        conn.execute("UPDATE reminders SET last_sent_date = ? WHERE id = ?", (today_str, reminder_id))


def log_message(phone: str, role: str, content: str) -> None:
    """Guarda un turno de conversación (para poder consultarlas después; no es la memoria
    que usa el modelo, esa sigue viviendo en proceso)."""
    with _connection() as conn:
        conn.execute(
            "INSERT INTO conversations (phone, role, content) VALUES (?, ?, ?)",
            (phone, role, content),
        )


def get_conversation(phone: str, limit: int = 200) -> list:
    with _connection() as conn:
        rows = conn.execute(
            "SELECT role, content, created_at FROM conversations WHERE phone = ? ORDER BY id DESC LIMIT ?",
            (phone, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]


def record_checkin(phone: str, date_str: str, kind: str, responded: bool = True, energy_score: int = None, note: str = None) -> None:
    """Registra (o actualiza) un check-in del día para poder calcular el resumen semanal."""
    with _connection() as conn:
        existing = conn.execute(
            "SELECT id FROM checkins WHERE phone = ? AND date = ? AND kind = ?", (phone, date_str, kind)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE checkins SET responded = ?, energy_score = ?, note = ? WHERE id = ?",
                (int(responded), energy_score, note, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO checkins (phone, date, kind, responded, energy_score, note) VALUES (?, ?, ?, ?, ?, ?)",
                (phone, date_str, kind, int(responded), energy_score, note),
            )


def get_weekly_summary(phone: str, start_date_str: str, end_date_str: str) -> dict:
    """Calcula cumplimiento (días con al menos un check-in respondido) y energía promedio
    de la semana entre start_date_str y end_date_str (ambos incluidos, formato YYYY-MM-DD)."""
    with _connection() as conn:
        rows = conn.execute(
            """
            SELECT date, energy_score FROM checkins
            WHERE phone = ? AND date BETWEEN ? AND ? AND responded = 1
            """,
            (phone, start_date_str, end_date_str),
        ).fetchall()
        days_completed = len({row["date"] for row in rows})
        energy_scores = [row["energy_score"] for row in rows if row["energy_score"] is not None]
        avg_energy = round(sum(energy_scores) / len(energy_scores), 1) if energy_scores else None
        return {"days_completed": days_completed, "avg_energy": avg_energy}


def create_quote(phone: str, product: str = None, amount_expected: str = None) -> int:
    """Etapa 1: crea el registro de cotización (todavía sin ubicación ni cálculo)."""
    with _connection() as conn:
        cursor = conn.execute(
            "INSERT INTO orders (phone, product, amount_expected, status) VALUES (?, ?, ?, 'cotizando')",
            (phone, product, amount_expected),
        )
        return cursor.lastrowid


def get_active_order(phone: str):
    """Devuelve la orden/cotización más reciente de este teléfono que todavía no fue
    autorizada ni rechazada (sigue en curso, en cualquiera de las 2 etapas)."""
    with _connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM orders WHERE phone = ? AND status NOT IN ('autorizado', 'rechazado')
            ORDER BY id DESC LIMIT 1
            """,
            (phone,),
        ).fetchone()
        return dict(row) if row else None


def get_order(order_id: int):
    with _connection() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None


def set_quote_location(order_id: int, lat: float, lng: float, name: str = None) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE orders SET dest_lat = ?, dest_lng = ?, dest_location_name = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (lat, lng, name, order_id),
        )


def set_quote_calculation(order_id: int, weight_kg: float, distance_km: float, options_json: str) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE orders SET total_weight_kg = ?, distance_km = ?, shipping_options_json = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (weight_kg, distance_km, options_json, order_id),
        )


def mark_quote_pending_approval(order_id: int) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE orders SET status = 'cotizacion_pendiente_aprobacion', updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (order_id,),
        )


def approve_quote(phone: str, options_json_override: str = None) -> dict:
    """El número de cotización le da luz verde (Etapa 1). Si ajustó el precio, se le pasa el
    nuevo `shipping_options_json` ya recalculado; si no, se conserva el cálculo original."""
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE phone = ? AND status = 'cotizacion_pendiente_aprobacion' "
            "ORDER BY id DESC LIMIT 1",
            (phone,),
        ).fetchone()
        if not row:
            return None
        if options_json_override:
            conn.execute(
                "UPDATE orders SET status = 'cotizado', shipping_options_json = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (options_json_override, row["id"]),
            )
        else:
            conn.execute(
                "UPDATE orders SET status = 'cotizado', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row["id"],),
            )
        updated = conn.execute("SELECT * FROM orders WHERE id = ?", (row["id"],)).fetchone()
        return dict(updated)


def reject_quote(phone: str, motivo: str = None) -> dict:
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE phone = ? AND status = 'cotizacion_pendiente_aprobacion' "
            "ORDER BY id DESC LIMIT 1",
            (phone,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE orders SET status = 'rechazado', rejection_reason = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (motivo, row["id"]),
        )
        updated = conn.execute("SELECT * FROM orders WHERE id = ?", (row["id"],)).fetchone()
        return dict(updated)


def set_shipping_choice(order_id: int, choice_label: str, cost, eta: str = None) -> None:
    """Etapa 2 arranca aquí: el cliente ya eligió envío, la orden pasa a 'pendiente_pago' y
    queda lista para que TEO le dé los datos bancarios con el monto total."""
    with _connection() as conn:
        conn.execute(
            "UPDATE orders SET shipping_choice = ?, shipping_cost = ?, delivery_estimate = ?, "
            "status = 'pendiente_pago', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (choice_label, str(cost), eta, order_id),
        )


def set_delivery_notes(order_id: int, notes: str) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE orders SET delivery_notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (notes, order_id),
        )


def set_order_proof(order_id: int, media_id: str, vision_summary: str = None) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE orders SET proof_media_id = ?, vision_summary = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (media_id, vision_summary, order_id),
        )


def mark_order_in_review(order_id: int) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE orders SET status = 'en_revision', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (order_id,),
        )


def authorize_order(phone: str, delivery_estimate: str = None) -> dict:
    """Autoriza la orden activa de este teléfono (dada por el número de control humano) y
    devuelve la orden completa para poder reenviarla a logística y confirmarle al cliente. Si
    no se pasa `delivery_estimate`, se conserva el ETA que ya quedó fijado al elegir el envío
    en la Etapa 1 (el humano de control ya no necesita inventarlo de nuevo)."""
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE phone = ? AND status NOT IN ('autorizado', 'rechazado') "
            "ORDER BY id DESC LIMIT 1",
            (phone,),
        ).fetchone()
        if not row:
            return None
        final_estimate = delivery_estimate or row["delivery_estimate"]
        conn.execute(
            "UPDATE orders SET status = 'autorizado', delivery_estimate = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (final_estimate, row["id"]),
        )
        updated = conn.execute("SELECT * FROM orders WHERE id = ?", (row["id"],)).fetchone()
        return dict(updated)


def reject_order(phone: str, motivo: str = None) -> dict:
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE phone = ? AND status NOT IN ('autorizado', 'rechazado') "
            "ORDER BY id DESC LIMIT 1",
            (phone,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE orders SET status = 'rechazado', rejection_reason = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (motivo, row["id"]),
        )
        updated = conn.execute("SELECT * FROM orders WHERE id = ?", (row["id"],)).fetchone()
        return dict(updated)


def create_enrollment(
    phone: str, sponsor: str = None, sponsor_name: str = None, full_name: str = None, age: str = None
) -> int:
    with _connection() as conn:
        cursor = conn.execute(
            "INSERT INTO enrollments (phone, sponsor_distributor_number, sponsor_name, full_name, age) "
            "VALUES (?, ?, ?, ?, ?)",
            (phone, sponsor, sponsor_name, full_name, age),
        )
        return cursor.lastrowid


def get_active_enrollment(phone: str):
    """Devuelve la inscripción más reciente de este teléfono que todavía no se envió al
    equipo (sigue en curso: falta INE, falta rostro, o ya está completa pero sin enviar)."""
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM enrollments WHERE phone = ? AND status != 'enviado' ORDER BY id DESC LIMIT 1",
            (phone,),
        ).fetchone()
        return dict(row) if row else None


def get_enrollment(enrollment_id: int):
    with _connection() as conn:
        row = conn.execute("SELECT * FROM enrollments WHERE id = ?", (enrollment_id,)).fetchone()
        return dict(row) if row else None


def set_enrollment_ine(enrollment_id: int, media_id: str) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE enrollments SET ine_media_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (media_id, enrollment_id),
        )


def set_enrollment_face(enrollment_id: int, media_id: str) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE enrollments SET face_media_id = ?, status = 'completo', updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (media_id, enrollment_id),
        )


def mark_enrollment_sent(enrollment_id: int) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE enrollments SET status = 'enviado', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (enrollment_id,),
        )


def confirm_enrollment(phone: str, tiempo_reflejo: str) -> dict:
    """Belén o Yeni confirman que ya dieron de alta al aspirante (buscado por los últimos 10
    dígitos, igual que las órdenes de pago, ya que el humano puede teclear el número con o sin
    prefijo). Solo aplica a inscripciones ya enviadas al equipo (status='enviado')."""
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM enrollments WHERE phone = ? AND status = 'enviado' ORDER BY id DESC LIMIT 1",
            (phone,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE enrollments SET status = 'confirmado', reflection_time = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (tiempo_reflejo, row["id"]),
        )
        updated = conn.execute("SELECT * FROM enrollments WHERE id = ?", (row["id"],)).fetchone()
        return dict(updated)


def upsert_stock(product: str, quantity: int, updated_by: str = None) -> None:
    with _connection() as conn:
        existing = conn.execute("SELECT product FROM inventory WHERE product = ?", (product,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE inventory SET quantity = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP WHERE product = ?",
                (quantity, updated_by, product),
            )
        else:
            conn.execute(
                "INSERT INTO inventory (product, quantity, updated_by) VALUES (?, ?, ?)",
                (product, quantity, updated_by),
            )


def get_all_stock() -> list:
    with _connection() as conn:
        rows = conn.execute("SELECT * FROM inventory ORDER BY product ASC").fetchall()
        return [dict(row) for row in rows]


def increment_stock(product: str, delta: int, updated_by: str = None) -> None:
    """Suma `delta` a la cantidad actual (a diferencia de `upsert_stock`, que la reemplaza) —
    para cuando un lote de producción se agrega al inventario ya existente."""
    with _connection() as conn:
        row = conn.execute("SELECT quantity FROM inventory WHERE product = ?", (product,)).fetchone()
        current = row["quantity"] if row else 0
        new_quantity = current + delta
        if row:
            conn.execute(
                "UPDATE inventory SET quantity = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP WHERE product = ?",
                (new_quantity, updated_by, product),
            )
        else:
            conn.execute(
                "INSERT INTO inventory (product, quantity, updated_by) VALUES (?, ?, ?)",
                (product, new_quantity, updated_by),
            )


def create_production_batch(product: str, quantity: int, warranty_band: str, reported_by: str = None) -> int:
    with _connection() as conn:
        cursor = conn.execute(
            "INSERT INTO production_batches (product, quantity, warranty_band, reported_by) VALUES (?, ?, ?, ?)",
            (product, quantity, warranty_band, reported_by),
        )
        return cursor.lastrowid


def get_daily_metrics(date_str: str, exclude_phones: set = None) -> dict:
    """Métricas operativas del día (formato YYYY-MM-DD) para el reporte ejecutivo de la CEO.
    `exclude_phones`: últimos 10 dígitos de números a excluir (ej. staff interno probando su
    propio flujo de distribuidor) para que no se cuenten como actividad real de clientes."""
    exclude_clause = ""
    exclude_params: tuple = ()
    if exclude_phones:
        placeholders = ", ".join("?" for _ in exclude_phones)
        exclude_clause = f" AND substr(phone, -10) NOT IN ({placeholders})"
        exclude_params = tuple(exclude_phones)

    with _connection() as conn:
        quotes_created = conn.execute(
            f"SELECT COUNT(*) AS n FROM orders WHERE date(created_at) = ?{exclude_clause}",
            (date_str, *exclude_params),
        ).fetchone()["n"]
        authorized = conn.execute(
            f"SELECT COUNT(*) AS n FROM orders WHERE status = 'autorizado' AND date(updated_at) = ?{exclude_clause}",
            (date_str, *exclude_params),
        ).fetchone()["n"]
        authorized_rows = conn.execute(
            f"SELECT amount_expected, shipping_cost FROM orders "
            f"WHERE status = 'autorizado' AND date(updated_at) = ?{exclude_clause}",
            (date_str, *exclude_params),
        ).fetchall()
        total_sales = 0.0
        for row in authorized_rows:
            for value in (row["amount_expected"], row["shipping_cost"]):
                if value is None:
                    continue
                try:
                    total_sales += float(str(value).replace("$", "").replace(",", ""))
                except ValueError:
                    continue
        new_enrollments = conn.execute(
            f"SELECT COUNT(*) AS n FROM enrollments WHERE date(created_at) = ?{exclude_clause}",
            (date_str, *exclude_params),
        ).fetchone()["n"]
        confirmed_enrollments = conn.execute(
            f"SELECT COUNT(*) AS n FROM enrollments WHERE status = 'confirmado' AND date(updated_at) = ?{exclude_clause}",
            (date_str, *exclude_params),
        ).fetchone()["n"]
        return {
            "cotizaciones_creadas": quotes_created,
            "pagos_autorizados": authorized,
            "ventas_totales_estimadas": round(total_sales, 2),
            "inscripciones_nuevas": new_enrollments,
            "inscripciones_confirmadas": confirmed_enrollments,
        }


def get_recent_conversations_sample(limit: int = 200, exclude_phones: set = None) -> list:
    """Muestra reciente de mensajes de CUALQUIER cliente (a diferencia de `get_conversation`,
    que es por teléfono), para el análisis de insights de marketing. `exclude_phones`: últimos
    10 dígitos de números a excluir (ej. staff interno probando su propio flujo de distribuidor)."""
    exclude_clause = ""
    exclude_params: tuple = ()
    if exclude_phones:
        placeholders = ", ".join("?" for _ in exclude_phones)
        exclude_clause = f" WHERE substr(phone, -10) NOT IN ({placeholders})"
        exclude_params = tuple(exclude_phones)

    with _connection() as conn:
        rows = conn.execute(
            f"SELECT phone, role, content, created_at FROM conversations{exclude_clause} "
            f"ORDER BY id DESC LIMIT ?",
            (*exclude_params, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]
