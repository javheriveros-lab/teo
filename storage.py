import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# En producción (Railway), DB_DIR apunta a un volumen persistente para que teo.db sobreviva
# redeploys y reinicios del contenedor. En local, sin esa variable, usa la carpeta del proyecto.
DB_PATH = Path(os.environ.get("DB_DIR", Path(__file__).parent)) / "teo.db"

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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clinical_escalations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                case_summary TEXT NOT NULL,
                questions TEXT NOT NULL,
                alma_response TEXT,
                status TEXT NOT NULL DEFAULT 'pendiente' CHECK (status IN ('pendiente', 'respondido')),
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                answered_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                video_url TEXT NOT NULL,
                created_by TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                last_sent_date TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Embudo de venta: consentimiento para tips/promociones (NULL = no se ha preguntado),
        # consentimiento para recordatorios del plan, interés en el negocio y entrega recibida.
        _ensure_column(conn, "customers", "marketing_optin", "marketing_optin INTEGER")
        _ensure_column(conn, "customers", "reminders_optin", "reminders_optin INTEGER")
        _ensure_column(conn, "customers", "business_interest", "business_interest TEXT")
        _ensure_column(conn, "orders", "received_at", "received_at TEXT")
        # Validación de ID de distribuidor con Ventas (Belén) antes de aplicar cualquier descuento.
        for column, ddl in (
            ("distributor_id", "distributor_id TEXT"),
            ("discount_status", "discount_status TEXT"),
            ("discount_pct", "discount_pct REAL"),
            ("discount_scope", "discount_scope TEXT"),
            ("discount_notes", "discount_notes TEXT"),
            ("amount_public", "amount_public TEXT"),
            ("discount_requested_at", "discount_requested_at TEXT"),
            ("discount_reminded_at", "discount_reminded_at TEXT"),
            ("discount_reminders", "discount_reminders INTEGER NOT NULL DEFAULT 0"),
            ("discount_resolved_at", "discount_resolved_at TEXT"),
        ):
            _ensure_column(conn, "orders", column, ddl)
        # Flujo de alta de socios con pago (Ara) e identidad (Belén/Yeni). `flow_stage` convive con el
        # `status` original (su CHECK no se puede ampliar en SQLite); NULL = inscripción del flujo viejo.
        for column, ddl in (
            ("flow_stage", "flow_stage TEXT"),
            ("origin", "origin TEXT"),
            ("sponsor_phone", "sponsor_phone TEXT"),
            ("order_id", "order_id INTEGER"),
            ("payment_approved_by", "payment_approved_by TEXT"),
            ("ping_message_id", "ping_message_id TEXT"),
            ("ping_failed_notified", "ping_failed_notified INTEGER NOT NULL DEFAULT 0"),
            ("approved_by", "approved_by TEXT"),
            ("rejection_reason", "rejection_reason TEXT"),
            ("activated_at", "activated_at TEXT"),
        ):
            _ensure_column(conn, "enrollments", column, ddl)
        _ensure_column(conn, "orders", "kind", "kind TEXT NOT NULL DEFAULT 'pedido'")
        _ensure_column(conn, "orders", "enrollment_id", "enrollment_id INTEGER")
        for column, ddl in (
            ("member_since", "member_since TEXT"),
            ("member_discount_pct", "member_discount_pct REAL"),
            ("member_enrollment_id", "member_enrollment_id INTEGER"),
            # Ficha verificada (socio activado o distribuidor validado por Belén/Yeni): descuento
            # automático en cada cotización sin volver a pedir el ID, y liga con su líder para comisiones.
            ("empresario_id", "empresario_id TEXT"),
            ("discount_scope", "discount_scope TEXT"),
            ("discount_notes", "discount_notes TEXT"),
            ("discount_verified_by", "discount_verified_by TEXT"),
            ("discount_verified_at", "discount_verified_at TEXT"),
            ("profile_kind", "profile_kind TEXT"),
            ("sponsor_empresario_id", "sponsor_empresario_id TEXT"),
            ("sponsor_phone", "sponsor_phone TEXT"),
            ("commission_pct", "commission_pct REAL"),
            ("address", "address TEXT"),
        ):
            _ensure_column(conn, "customers", column, ddl)
        _ensure_column(conn, "enrollments", "address", "address TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS network_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL UNIQUE,
                buyer_phone TEXT NOT NULL,
                buyer_name TEXT,
                buyer_empresario_id TEXT,
                leader_empresario_id TEXT NOT NULL,
                product TEXT,
                amount_public REAL,
                discount_pct REAL,
                amount_paid REAL NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS commission_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_key TEXT NOT NULL UNIQUE,
                period_start_utc TEXT NOT NULL,
                period_end_utc TEXT NOT NULL,
                label TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'revision_control',
                validated_by TEXT,
                validated_at TEXT,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS commission_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                leader_empresario_id TEXT NOT NULL,
                leader_phone TEXT,
                leader_name TEXT,
                purchases INTEGER NOT NULL DEFAULT 0,
                volume REAL NOT NULL DEFAULT 0,
                commission_pct REAL,
                commission REAL,
                adjusted INTEGER NOT NULL DEFAULT 0,
                proof_media_id TEXT,
                delivery_status TEXT NOT NULL DEFAULT 'pendiente',
                delivered_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_last10 TEXT NOT NULL,
                kind TEXT NOT NULL,
                ref_id INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                delivered_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inbound_activity (
                phone_last10 TEXT PRIMARY KEY,
                last_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS campaign_content (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK (kind IN ('promocion', 'video_negocio')),
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                url TEXT,
                created_by TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS followup_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                source TEXT NOT NULL CHECK (source IN ('habito', 'promocion', 'video_negocio')),
                item_id INTEGER NOT NULL,
                sent_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_followup_log_phone ON followup_log (phone, id)")
        # Programa de coaching comercial para Ventas (Belén): videos curados, estrategias
        # co-creadas con TEO (con historial de versiones) y planes de 30 días medibles.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS coach_resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL UNIQUE,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                channel TEXT,
                pillar TEXT,
                query TEXT,
                lesson TEXT,
                rating INTEGER,
                note TEXT,
                sent_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'borrador',
                content TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                result TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id INTEGER NOT NULL,
                version INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sales_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                strategy_id INTEGER,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'activo',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL,
                metric TEXT NOT NULL,
                target REAL NOT NULL,
                owner TEXT,
                manual_value REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS coach_daily_log (
                date TEXT PRIMARY KEY,
                resource_id INTEGER,
                sent_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL,
                week INTEGER NOT NULL,
                description TEXT NOT NULL,
                owner TEXT,
                due_date TEXT,
                status TEXT NOT NULL DEFAULT 'pendiente'
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


def set_reminders_active(phone: str, active: bool) -> int:
    """Activa/desactiva los recordatorios ya guardados del cliente. Devuelve cuántos había."""
    with _connection() as conn:
        cursor = conn.execute("UPDATE reminders SET active = ? WHERE phone = ?", (int(active), phone))
        return cursor.rowcount


def _set_customer_field(phone: str, column: str, value) -> None:
    with _connection() as conn:
        existing = conn.execute("SELECT phone FROM customers WHERE phone = ?", (phone,)).fetchone()
        if existing:
            conn.execute(f"UPDATE customers SET {column} = ? WHERE phone = ?", (value, phone))
        else:
            conn.execute(f"INSERT INTO customers (phone, {column}) VALUES (?, ?)", (phone, value))


def set_marketing_optin(phone: str, accepted: bool) -> None:
    _set_customer_field(phone, "marketing_optin", int(accepted))


def set_reminders_optin(phone: str, accepted: bool) -> None:
    _set_customer_field(phone, "reminders_optin", int(accepted))


BUSINESS_INTEREST_VALUES = ("interesado", "no", "lider")


def set_business_interest(phone: str, value: str) -> None:
    """'interesado' = no aceptó todavía pero quiere recibir videos de negocio; 'no' = no le
    interesa; 'lider' = arrancó el entrenamiento de líder (curso Comparte tu Transformación)."""
    assert value in BUSINESS_INTEREST_VALUES, value
    _set_customer_field(phone, "business_interest", value)


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


# TEO solo inicia mensajes (recordatorios, hábito diario) con quien ya concretó una compra:
# una orden autorizada por el equipo de control. A los prospectos nunca les escribe primero.
_HAS_PURCHASE_SQL = "EXISTS (SELECT 1 FROM orders o WHERE o.phone = r.phone AND o.status = 'autorizado')"


def has_confirmed_purchase(phone: str) -> bool:
    with _connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM orders WHERE phone = ? AND status = 'autorizado' LIMIT 1", (phone,)
        ).fetchone()
        return row is not None


def get_due_reminders(hour: int, minute: int, today_str: str) -> list:
    with _connection() as conn:
        rows = conn.execute(
            f"""
            SELECT r.id, r.phone, r.kind, r.meal_label FROM reminders r
            LEFT JOIN customers c ON c.phone = r.phone
            WHERE r.active = 1 AND r.hour = ? AND r.minute = ?
              AND (r.last_sent_date IS NULL OR r.last_sent_date != ?)
              AND COALESCE(c.paused, 0) = 0
              AND {_HAS_PURCHASE_SQL}
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


def get_latest_authorized_order(phone: str):
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE phone = ? AND status = 'autorizado' AND COALESCE(kind, 'pedido') != 'inscripcion' "
            "ORDER BY id DESC LIMIT 1", (phone,)
        ).fetchone()
        return dict(row) if row else None


def mark_order_received(phone: str):
    """Marca como recibido el último pedido autorizado del cliente. Devuelve la orden, o None
    si no tiene un pedido autorizado pendiente de recibir."""
    with _connection() as conn:
        row = conn.execute(
            "SELECT id FROM orders WHERE phone = ? AND status = 'autorizado' AND received_at IS NULL "
            "AND COALESCE(kind, 'pedido') != 'inscripcion' "
            "ORDER BY id DESC LIMIT 1",
            (phone,),
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE orders SET received_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
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
            "SELECT * FROM enrollments WHERE phone = ? AND status != 'enviado' AND flow_stage IS NULL "
            "ORDER BY id DESC LIMIT 1",
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
            "SELECT * FROM enrollments WHERE phone = ? AND status = 'enviado' AND flow_stage IS NULL "
            "ORDER BY id DESC LIMIT 1",
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


def create_clinical_escalation(phone: str, case_summary: str, questions: str) -> int:
    with _connection() as conn:
        cursor = conn.execute(
            "INSERT INTO clinical_escalations (phone, case_summary, questions) VALUES (?, ?, ?)",
            (phone, case_summary, questions),
        )
        return cursor.lastrowid


def answer_clinical_escalation(phone: str, alma_response: str) -> dict:
    """Alma responde un caso clínico escalado (buscado por los últimos 10 dígitos, igual que
    `confirm_enrollment`, ya que el humano puede teclear el número con o sin prefijo). Solo
    aplica al caso pendiente más reciente de ese teléfono."""
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM clinical_escalations WHERE phone = ? AND status = 'pendiente' ORDER BY id DESC LIMIT 1",
            (phone,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE clinical_escalations SET status = 'respondido', alma_response = ?, "
            "answered_at = CURRENT_TIMESTAMP WHERE id = ?",
            (alma_response, row["id"]),
        )
        updated = conn.execute("SELECT * FROM clinical_escalations WHERE id = ?", (row["id"],)).fetchone()
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


def create_habit(name: str, description: str, video_url: str, created_by: str = None) -> int:
    with _connection() as conn:
        cursor = conn.execute(
            "INSERT INTO habits (name, description, video_url, created_by) VALUES (?, ?, ?, ?)",
            (name, description, video_url, created_by),
        )
        return cursor.lastrowid


def get_habit(habit_id: int):
    with _connection() as conn:
        row = conn.execute("SELECT * FROM habits WHERE id = ?", (habit_id,)).fetchone()
        return dict(row) if row else None


def list_habits(active_only: bool = True) -> list:
    where = " WHERE active = 1" if active_only else ""
    with _connection() as conn:
        rows = conn.execute(f"SELECT * FROM habits{where} ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]


def deactivate_habit(habit_id: int) -> bool:
    with _connection() as conn:
        cursor = conn.execute("UPDATE habits SET active = 0 WHERE id = ? AND active = 1", (habit_id,))
        return cursor.rowcount > 0


def get_next_habit():
    """Hábito del día: primero los que nunca se han enviado (en orden de registro), después
    rota por el que lleva más tiempo sin enviarse."""
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM habits WHERE active = 1 "
            "ORDER BY last_sent_date IS NOT NULL, last_sent_date ASC, id ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def mark_habit_sent(habit_id: int, date_str: str) -> None:
    with _connection() as conn:
        conn.execute("UPDATE habits SET last_sent_date = ? WHERE id = ?", (date_str, habit_id))


def habit_sent_on(date_str: str) -> bool:
    """Persistido en BD (no en memoria) para que un reinicio/redeploy no duplique el envío del día."""
    with _connection() as conn:
        row = conn.execute("SELECT 1 FROM habits WHERE last_sent_date = ? LIMIT 1", (date_str,)).fetchone()
        return row is not None


def get_habit_recipients() -> list:
    """Clientes con compra confirmada que aceptaron su plan calendarizado (tienen recordatorios
    activos) y no escribieron PAUSA — son los únicos que ya optaron por recibir mensajes diarios
    de TEO."""
    with _connection() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT r.phone FROM reminders r
            LEFT JOIN customers c ON c.phone = r.phone
            WHERE r.active = 1 AND COALESCE(c.paused, 0) = 0
              AND {_HAS_PURCHASE_SQL}
            """
        ).fetchall()
        return [row["phone"] for row in rows]


CAMPAIGN_KINDS = ("promocion", "video_negocio")


def create_campaign_content(kind: str, title: str, description: str, url: str = None, created_by: str = None) -> int:
    assert kind in CAMPAIGN_KINDS, kind
    with _connection() as conn:
        cursor = conn.execute(
            "INSERT INTO campaign_content (kind, title, description, url, created_by) VALUES (?, ?, ?, ?, ?)",
            (kind, title, description, url, created_by),
        )
        return cursor.lastrowid


def list_campaign_content(kind: str = None) -> list:
    where, params = "WHERE active = 1", ()
    if kind:
        where, params = "WHERE active = 1 AND kind = ?", (kind,)
    with _connection() as conn:
        rows = conn.execute(f"SELECT * FROM campaign_content {where} ORDER BY id ASC", params).fetchall()
        return [dict(row) for row in rows]


def deactivate_campaign_content(content_id: int) -> bool:
    with _connection() as conn:
        cursor = conn.execute("UPDATE campaign_content SET active = 0 WHERE id = ? AND active = 1", (content_id,))
        return cursor.rowcount > 0


FOLLOWUP_SILENCE = "-1 day"      # un prospecto entra a seguimiento tras 24 h sin responder
FOLLOWUP_MAX_AGE = "-60 days"    # y deja de recibir seguimiento si lleva 60 días sin escribir
FOLLOWUP_MIN_GAP = "-2 days"     # nunca más de un mensaje de seguimiento cada 2 días


def get_followup_prospects() -> list:
    """Lista de seguimiento comercial: prospectos (sin compra confirmada ni pago en revisión)
    que aceptaron recibir tips/promociones, no escribieron PAUSA y llevan más de 24 h sin
    responder — incluye a quien recibió datos de pago y nunca mandó su comprobante."""
    with _connection() as conn:
        rows = conn.execute(
            f"""
            SELECT c.phone FROM customers c
            WHERE c.marketing_optin = 1 AND COALESCE(c.paused, 0) = 0
              AND NOT EXISTS (
                  SELECT 1 FROM orders o WHERE o.phone = c.phone AND o.status IN ('autorizado', 'en_revision')
              )
              AND (SELECT MAX(m.created_at) FROM conversations m WHERE m.phone = c.phone AND m.role = 'user')
                  BETWEEN datetime('now', '{FOLLOWUP_MAX_AGE}') AND datetime('now', '{FOLLOWUP_SILENCE}')
              AND NOT EXISTS (
                  SELECT 1 FROM followup_log f WHERE f.phone = c.phone AND f.source IN ('habito', 'promocion')
                    AND f.sent_at > datetime('now', '{FOLLOWUP_MIN_GAP}')
              )
            """
        ).fetchall()
        return [row["phone"] for row in rows]


def get_business_followup_list() -> list:
    """Interesados en el negocio que no arrancaron todavía y aceptaron recibir videos: uno por
    semana como máximo."""
    with _connection() as conn:
        rows = conn.execute(
            """
            SELECT c.phone FROM customers c
            WHERE c.business_interest = 'interesado' AND COALESCE(c.paused, 0) = 0
              AND NOT EXISTS (
                  SELECT 1 FROM followup_log f WHERE f.phone = c.phone AND f.source = 'video_negocio'
                    AND f.sent_at > datetime('now', '-6 days')
              )
            """
        ).fetchall()
        return [row["phone"] for row in rows]


def next_followup_item(phone: str, sources: tuple):
    """Siguiente contenido para este teléfono entre `sources` ('habito', 'promocion',
    'video_negocio'): primero lo que nunca le ha llegado (alternando de fuente respecto al último
    envío), y si ya recibió todo, lo que lleva más tiempo sin recibir. Lee los hábitos sin
    modificarlos (no toca last_sent_date, que es de la rotación diaria de los compradores)."""
    items = []
    if "habito" in sources:
        items += [
            {"source": "habito", "id": h["id"], "title": h["name"], "description": h["description"], "url": h["video_url"]}
            for h in sorted(list_habits(), key=lambda h: h["id"])
        ]
    for kind in CAMPAIGN_KINDS:
        if kind in sources:
            items += [
                {"source": kind, "id": c["id"], "title": c["title"], "description": c["description"], "url": c["url"]}
                for c in list_campaign_content(kind)
            ]
    if not items:
        return None

    with _connection() as conn:
        rows = conn.execute(
            "SELECT source, item_id, MAX(id) AS last_id FROM followup_log WHERE phone = ? GROUP BY source, item_id",
            (phone,),
        ).fetchall()
        last = conn.execute(
            "SELECT source FROM followup_log WHERE phone = ? ORDER BY id DESC LIMIT 1", (phone,)
        ).fetchone()
    sent = {(row["source"], row["item_id"]): row["last_id"] for row in rows}
    last_source = last["source"] if last else None

    unsent = [item for item in items if (item["source"], item["id"]) not in sent]
    if unsent:
        other_source = [item for item in unsent if item["source"] != last_source]
        return (other_source or unsent)[0]
    return min(items, key=lambda item: sent[(item["source"], item["id"])])


def log_followup(phone: str, source: str, item_id: int) -> None:
    with _connection() as conn:
        conn.execute("INSERT INTO followup_log (phone, source, item_id) VALUES (?, ?, ?)", (phone, source, item_id))


# ---------------------------------------------------------------------------
# Coaching comercial (Belén): datos reales, videos, estrategias y planes de 30 días
# ---------------------------------------------------------------------------

AUTO_METRICS = {
    "pedidos_autorizados": "Pedidos autorizados",
    "monto_vendido": "Monto vendido en productos ($)",
    "inscripciones_confirmadas": "Inscripciones confirmadas",
    "cotizaciones": "Cotizaciones iniciadas",
    "recompras": "Recompras (2a compra o más del mismo cliente)",
    "prospectos_reactivados": "Prospectos que respondieron tras un seguimiento",
}
STRATEGY_KINDS = ("reactivacion", "recompra", "incentivo", "dinamica_equipo", "prospeccion", "otra")
STRATEGY_STATUSES = ("borrador", "aprobada", "en_ejecucion", "terminada", "descartada")
_OPEN_QUOTE_STATUSES = "('cotizando', 'cotizacion_pendiente_aprobacion', 'cotizado', 'pendiente_pago')"


def _exclude(exclude_phones, column: str):
    if not exclude_phones:
        return "", ()
    placeholders = ", ".join("?" for _ in exclude_phones)
    return f" AND substr({column}, -10) NOT IN ({placeholders})", tuple(exclude_phones)


def _money(value) -> float:
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def get_sales_snapshot(days: int = 30, exclude_phones: set = None, sample_size: int = 10) -> dict:
    """Foto comercial de los últimos `days` días, solo con datos reales de la base. Las listas de
    teléfonos se recortan a `sample_size` para que quepan en un mensaje."""
    since = f"-{int(days)} days"
    ex_o, p_o = _exclude(exclude_phones, "o.phone")
    ex_c, p_c = _exclude(exclude_phones, "c.phone")
    ex_e, p_e = _exclude(exclude_phones, "e.phone")
    ex_f, p_f = _exclude(exclude_phones, "f.phone")
    ex_m, p_m = _exclude(exclude_phones, "m.phone")
    with _connection() as conn:
        by_status = {
            row["status"]: row["n"]
            for row in conn.execute(
                f"SELECT o.status, COUNT(*) AS n FROM orders o "
                f"WHERE o.created_at >= datetime('now', ?){ex_o} GROUP BY o.status",
                (since, *p_o),
            )
        }
        authorized = conn.execute(
            f"SELECT o.product, o.amount_expected FROM orders o WHERE o.status = 'autorizado' "
            f"AND o.updated_at >= datetime('now', ?){ex_o}",
            (since, *p_o),
        ).fetchall()
        products: dict = {}
        for row in authorized:
            name = (row["product"] or "sin especificar").strip()
            products[name] = products.get(name, 0) + 1
        top_products = sorted(products.items(), key=lambda item: -item[1])[:5]

        stalled = [
            row["phone"] for row in conn.execute(
                f"SELECT DISTINCT o.phone FROM orders o WHERE o.status IN {_OPEN_QUOTE_STATUSES} "
                f"AND o.updated_at < datetime('now', '-3 days') "
                f"AND o.updated_at >= datetime('now', '-60 days'){ex_o}",
                p_o,
            )
        ]
        repurchase = [
            row["phone"] for row in conn.execute(
                f"SELECT o.phone FROM orders o WHERE o.status = 'autorizado'{ex_o} GROUP BY o.phone "
                f"HAVING MAX(o.updated_at) BETWEEN datetime('now', '-90 days') AND datetime('now', '-30 days')",
                p_o,
            )
        ]
        inactive_partners = [
            row["phone"] for row in conn.execute(
                f"SELECT DISTINCT e.phone FROM enrollments e WHERE e.status = 'confirmado'{ex_e} "
                f"AND NOT EXISTS (SELECT 1 FROM orders o WHERE substr(o.phone, -10) = substr(e.phone, -10) "
                f"AND o.status = 'autorizado' AND o.updated_at >= datetime('now', '-45 days'))",
                p_e,
            )
        ]
        enrollments_confirmed = conn.execute(
            f"SELECT COUNT(*) AS n FROM enrollments e WHERE e.status = 'confirmado' "
            f"AND e.updated_at >= datetime('now', ?){ex_e}",
            (since, *p_e),
        ).fetchone()["n"]
        business = {
            row["business_interest"]: row["n"]
            for row in conn.execute(
                f"SELECT c.business_interest, COUNT(*) AS n FROM customers c "
                f"WHERE c.business_interest IS NOT NULL{ex_c} GROUP BY c.business_interest",
                p_c,
            )
        }
        followups_sent = conn.execute(
            f"SELECT COUNT(*) AS n FROM followup_log f WHERE f.sent_at >= datetime('now', ?){ex_f}",
            (since, *p_f),
        ).fetchone()["n"]
        active_chats = conn.execute(
            f"SELECT COUNT(DISTINCT m.phone) AS n FROM conversations m WHERE m.role = 'user' "
            f"AND m.created_at >= datetime('now', ?){ex_m}",
            (since, *p_m),
        ).fetchone()["n"]

    return {
        "dias": int(days),
        "clientes_que_escribieron": active_chats,
        "pedidos_por_estado": by_status,
        "pedidos_autorizados": len(authorized),
        "monto_vendido": round(sum(_money(row["amount_expected"]) for row in authorized), 2),
        "productos_mas_vendidos": top_products,
        "cotizaciones_atascadas": {"total": len(stalled), "telefonos": stalled[:sample_size]},
        "candidatos_recompra_30_90d": {"total": len(repurchase), "telefonos": repurchase[:sample_size]},
        "socios_inactivos_45d": {"total": len(inactive_partners), "telefonos": inactive_partners[:sample_size]},
        "inscripciones_confirmadas": enrollments_confirmed,
        "interes_negocio": business,
        "seguimientos_enviados": followups_sent,
        "prospectos_en_seguimiento_hoy": len(get_followup_prospects()),
    }


def get_metric_actual(metric: str, start_date: str, end_date: str, exclude_phones: set = None) -> float:
    """Valor real de una métrica automática entre dos fechas (YYYY-MM-DD, inclusivas)."""
    ex_o, p_o = _exclude(exclude_phones, "o.phone")
    window = (start_date, end_date)
    with _connection() as conn:
        if metric == "pedidos_autorizados":
            return conn.execute(
                f"SELECT COUNT(*) AS n FROM orders o WHERE o.status = 'autorizado' "
                f"AND date(o.updated_at) BETWEEN ? AND ?{ex_o}", (*window, *p_o),
            ).fetchone()["n"]
        if metric == "monto_vendido":
            rows = conn.execute(
                f"SELECT o.amount_expected FROM orders o WHERE o.status = 'autorizado' "
                f"AND date(o.updated_at) BETWEEN ? AND ?{ex_o}", (*window, *p_o),
            ).fetchall()
            return round(sum(_money(row["amount_expected"]) for row in rows), 2)
        if metric == "cotizaciones":
            return conn.execute(
                f"SELECT COUNT(*) AS n FROM orders o WHERE date(o.created_at) BETWEEN ? AND ?{ex_o}",
                (*window, *p_o),
            ).fetchone()["n"]
        if metric == "recompras":
            return conn.execute(
                f"SELECT COUNT(*) AS n FROM orders o WHERE o.status = 'autorizado' "
                f"AND date(o.updated_at) BETWEEN ? AND ?{ex_o} AND EXISTS (SELECT 1 FROM orders p "
                f"WHERE p.phone = o.phone AND p.status = 'autorizado' AND p.id < o.id)",
                (*window, *p_o),
            ).fetchone()["n"]
        if metric == "inscripciones_confirmadas":
            ex_e, p_e = _exclude(exclude_phones, "e.phone")
            return conn.execute(
                f"SELECT COUNT(*) AS n FROM enrollments e WHERE e.status = 'confirmado' "
                f"AND date(e.updated_at) BETWEEN ? AND ?{ex_e}", (*window, *p_e),
            ).fetchone()["n"]
        if metric == "prospectos_reactivados":
            ex_f, p_f = _exclude(exclude_phones, "f.phone")
            return conn.execute(
                f"SELECT COUNT(DISTINCT f.phone) AS n FROM followup_log f WHERE date(f.sent_at) <= ?{ex_f} "
                f"AND EXISTS (SELECT 1 FROM conversations m WHERE m.phone = f.phone AND m.role = 'user' "
                f"AND m.created_at > f.sent_at AND date(m.created_at) BETWEEN ? AND ?)",
                (end_date, *p_f, *window),
            ).fetchone()["n"]
    raise ValueError(f"Métrica desconocida: {metric}")


# --- Videos curados para Belén ---

def coach_video_known(video_id: str) -> bool:
    with _connection() as conn:
        return conn.execute("SELECT 1 FROM coach_resources WHERE video_id = ?", (video_id,)).fetchone() is not None


def save_coach_resource(video: dict, pillar: str, query: str, lesson: str) -> int:
    """Guarda un video curado (o actualiza su lección si ya existía) y devuelve su id."""
    with _connection() as conn:
        conn.execute(
            """
            INSERT INTO coach_resources (video_id, url, title, channel, pillar, query, lesson)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET lesson = excluded.lesson
            """,
            (video["video_id"], video["url"], video["title"], video.get("channel"), pillar, query, lesson),
        )
        return conn.execute(
            "SELECT id FROM coach_resources WHERE video_id = ?", (video["video_id"],)
        ).fetchone()["id"]


def mark_coach_resource_sent(resource_id: int) -> None:
    with _connection() as conn:
        conn.execute("UPDATE coach_resources SET sent_at = CURRENT_TIMESTAMP WHERE id = ?", (resource_id,))


def coach_daily_sent_on(date_str: str) -> bool:
    with _connection() as conn:
        return conn.execute("SELECT 1 FROM coach_daily_log WHERE date = ?", (date_str,)).fetchone() is not None


def log_coach_daily(date_str: str, resource_id: int = None) -> None:
    with _connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO coach_daily_log (date, resource_id) VALUES (?, ?)", (date_str, resource_id)
        )


def get_coach_resource(resource_id: int):
    with _connection() as conn:
        row = conn.execute("SELECT * FROM coach_resources WHERE id = ?", (resource_id,)).fetchone()
        return dict(row) if row else None


def rate_coach_resource(resource_id: int, useful: bool, note: str = None) -> bool:
    with _connection() as conn:
        cursor = conn.execute(
            "UPDATE coach_resources SET rating = ?, note = COALESCE(?, note) WHERE id = ?",
            (1 if useful else -1, note, resource_id),
        )
        return cursor.rowcount > 0


def get_fallback_coach_resource(min_days_since_sent: int = 30):
    """Respaldo cuando YouTube no responde: un video bien calificado por Belén que no se le
    haya repetido en los últimos `min_days_since_sent` días."""
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM coach_resources WHERE rating = 1 "
            "AND (sent_at IS NULL OR sent_at < datetime('now', ?)) ORDER BY sent_at LIMIT 1",
            (f"-{int(min_days_since_sent)} days",),
        ).fetchone()
        return dict(row) if row else None


def list_recent_coach_resources(limit: int = 5) -> list:
    with _connection() as conn:
        rows = conn.execute(
            "SELECT id, title, channel, pillar, url, lesson, rating, sent_at FROM coach_resources "
            "WHERE sent_at IS NOT NULL ORDER BY sent_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


# --- Estrategias co-creadas ---

def save_strategy(title: str, kind: str, content: str, strategy_id: int = None):
    """Crea una estrategia o, con `strategy_id`, guarda una versión nueva (la anterior queda en
    strategy_revisions). Devuelve la estrategia resultante, o None si el id no existe."""
    kind = kind if kind in STRATEGY_KINDS else "otra"
    with _connection() as conn:
        if strategy_id is None:
            strategy_id = conn.execute(
                "INSERT INTO strategies (title, kind, content) VALUES (?, ?, ?)", (title, kind, content)
            ).lastrowid
        else:
            current = conn.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
            if not current:
                return None
            conn.execute(
                "INSERT INTO strategy_revisions (strategy_id, version, content) VALUES (?, ?, ?)",
                (strategy_id, current["version"], current["content"]),
            )
            conn.execute(
                "UPDATE strategies SET title = ?, kind = ?, content = ?, version = version + 1, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (title or current["title"], kind, content, strategy_id),
            )
    return get_strategy(strategy_id)


def get_strategy(strategy_id: int):
    with _connection() as conn:
        row = conn.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
        return dict(row) if row else None


def list_strategies(include_closed: bool = False, limit: int = 15) -> list:
    clause = "" if include_closed else " WHERE status NOT IN ('terminada', 'descartada')"
    with _connection() as conn:
        rows = conn.execute(
            f"SELECT id, title, kind, status, version, result, updated_at FROM strategies{clause} "
            f"ORDER BY updated_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def count_strategy_revisions(strategy_id: int) -> int:
    with _connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM strategy_revisions WHERE strategy_id = ?", (strategy_id,)
        ).fetchone()["n"]


def set_strategy_status(strategy_id: int, status: str, result: str = None) -> bool:
    if status not in STRATEGY_STATUSES:
        raise ValueError(f"Estado inválido: {status}")
    with _connection() as conn:
        cursor = conn.execute(
            "UPDATE strategies SET status = ?, result = COALESCE(?, result), updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (status, result, strategy_id),
        )
        return cursor.rowcount > 0


# --- Planes de 30 días ---

def create_sales_plan(title: str, start_date: str, end_date: str, goals: list, actions: list,
                      strategy_id: int = None) -> int:
    """Crea el plan activo (cierra el anterior: solo hay uno a la vez). `goals`: dicts con
    metric/target/owner; `actions`: dicts con week/description/owner/due_date."""
    with _connection() as conn:
        conn.execute("UPDATE sales_plans SET status = 'cerrado' WHERE status = 'activo'")
        plan_id = conn.execute(
            "INSERT INTO sales_plans (title, strategy_id, start_date, end_date) VALUES (?, ?, ?, ?)",
            (title, strategy_id, start_date, end_date),
        ).lastrowid
        for goal in goals:
            conn.execute(
                "INSERT INTO plan_goals (plan_id, metric, target, owner) VALUES (?, ?, ?, ?)",
                (plan_id, goal["metric"], float(goal["target"]), goal.get("owner")),
            )
        for action in actions:
            conn.execute(
                "INSERT INTO plan_actions (plan_id, week, description, owner, due_date) VALUES (?, ?, ?, ?, ?)",
                (plan_id, int(action["week"]), action["description"], action.get("owner"), action.get("due_date")),
            )
        if strategy_id:
            conn.execute(
                "UPDATE strategies SET status = 'en_ejecucion', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (strategy_id,),
            )
        return plan_id


def get_active_plan():
    """Plan activo con sus metas y acciones (sin calcular el avance; eso lo hace quien llama
    con get_metric_actual)."""
    with _connection() as conn:
        plan = conn.execute(
            "SELECT * FROM sales_plans WHERE status = 'activo' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not plan:
            return None
        plan = dict(plan)
        plan["goals"] = [
            dict(row) for row in conn.execute("SELECT * FROM plan_goals WHERE plan_id = ? ORDER BY id", (plan["id"],))
        ]
        plan["actions"] = [
            dict(row) for row in conn.execute(
                "SELECT * FROM plan_actions WHERE plan_id = ? ORDER BY week, id", (plan["id"],)
            )
        ]
    return plan


def close_active_plan() -> bool:
    with _connection() as conn:
        return conn.execute("UPDATE sales_plans SET status = 'cerrado' WHERE status = 'activo'").rowcount > 0


def set_goal_manual_value(goal_id: int, value: float) -> bool:
    with _connection() as conn:
        return conn.execute(
            "UPDATE plan_goals SET manual_value = ? WHERE id = ?", (float(value), goal_id)
        ).rowcount > 0


def set_action_status(action_id: int, done: bool) -> bool:
    with _connection() as conn:
        return conn.execute(
            "UPDATE plan_actions SET status = ? WHERE id = ?", ("hecha" if done else "pendiente", action_id)
        ).rowcount > 0


def get_coach_learning() -> dict:
    """Memoria acumulada del programa: qué estrategias se han probado y cómo salieron, y qué
    videos le sirvieron (o no) a Belén."""
    with _connection() as conn:
        strategies = [
            dict(row) for row in conn.execute(
                "SELECT id, title, kind, status, version, result FROM strategies "
                "ORDER BY updated_at DESC, id DESC LIMIT 5"
            )
        ]
        liked = [
            dict(row) for row in conn.execute(
                "SELECT title, pillar, note FROM coach_resources WHERE rating = 1 ORDER BY id DESC LIMIT 3"
            )
        ]
        disliked = [
            dict(row) for row in conn.execute(
                "SELECT title, pillar, note FROM coach_resources WHERE rating = -1 ORDER BY id DESC LIMIT 3"
            )
        ]
    return {"estrategias_recientes": strategies, "videos_utiles": liked, "videos_no_utiles": disliked}


# ---------------------------------------------------------------------------
# Validación de ID de distribuidor (descuentos autorizados por Ventas)
# ---------------------------------------------------------------------------

DISCOUNT_STATUSES = ("pendiente", "aprobado", "rechazado", "cancelado")


def request_distributor_discount(order_id: int, distributor_id: str) -> None:
    """Marca la orden como pendiente de validación. El aviso inicial a Belén cuenta como el
    primer envío (discount_reminded_at) para medir los 15 minutos del siguiente recordatorio."""
    with _connection() as conn:
        conn.execute(
            "UPDATE orders SET distributor_id = ?, discount_status = 'pendiente', discount_pct = NULL, "
            "discount_scope = NULL, discount_notes = NULL, discount_requested_at = CURRENT_TIMESTAMP, "
            "discount_reminded_at = CURRENT_TIMESTAMP, discount_reminders = 0, discount_resolved_at = NULL, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (distributor_id, order_id),
        )


def resolve_distributor_discount(order_id: int, approved: bool, pct: float = None, scope: str = None,
                                 notes: str = None, discounted_amount: str = None):
    """Registra la respuesta de Belén. Si se aprueba, conserva el precio público en
    amount_public y deja en amount_expected el monto del producto con descuento (lo que ve
    control al revisar el depósito). Devuelve la orden actualizada."""
    with _connection() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order or order["discount_status"] != "pendiente":
            return None
        public_amount = order["amount_public"] or order["amount_expected"]
        conn.execute(
            "UPDATE orders SET discount_status = ?, discount_pct = ?, discount_scope = ?, discount_notes = ?, "
            "amount_public = ?, amount_expected = ?, discount_resolved_at = CURRENT_TIMESTAMP, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (
                "aprobado" if approved else "rechazado",
                pct if approved else None,
                scope if approved else None,
                notes,
                public_amount,
                discounted_amount if approved and discounted_amount else public_amount,
                order_id,
            ),
        )
        return dict(conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone())


def cancel_distributor_discount(order_id: int) -> bool:
    with _connection() as conn:
        return conn.execute(
            "UPDATE orders SET discount_status = 'cancelado', discount_resolved_at = CURRENT_TIMESTAMP, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND discount_status = 'pendiente'",
            (order_id,),
        ).rowcount > 0


def get_pending_discount_validations() -> list:
    with _connection() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE discount_status = 'pendiente' "
            "AND status NOT IN ('autorizado', 'rechazado') ORDER BY discount_requested_at"
        ).fetchall()
        return [dict(row) for row in rows]


def get_due_discount_reminders(minutes: int = 15) -> list:
    with _connection() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE discount_status = 'pendiente' "
            "AND status NOT IN ('autorizado', 'rechazado') "
            "AND (discount_reminded_at IS NULL OR discount_reminded_at <= datetime('now', ?)) "
            "ORDER BY discount_requested_at",
            (f"-{int(minutes)} minutes",),
        ).fetchall()
        return [dict(row) for row in rows]


def mark_discount_reminded(order_id: int) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE orders SET discount_reminded_at = CURRENT_TIMESTAMP, "
            "discount_reminders = discount_reminders + 1 WHERE id = ?",
            (order_id,),
        )


def get_pending_discount_order(phone_digits: str):
    """Orden con validación pendiente para un teléfono de cliente, tolerando 521/52/10 dígitos."""
    last10 = (phone_digits or "")[-10:]
    if len(last10) < 10:
        return None
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE discount_status = 'pendiente' AND substr(phone, -10) = ? "
            "AND status NOT IN ('autorizado', 'rechazado') ORDER BY id DESC LIMIT 1",
            (last10,),
        ).fetchone()
        return dict(row) if row else None


def _normalize_distributor_id(distributor_id: str) -> str:
    return "".join(ch for ch in (distributor_id or "").upper() if ch.isalnum())


def get_distributor_id_history(distributor_id: str, exclude_order_id: int = None) -> dict:
    """Usos anteriores del mismo ID (sin importar mayúsculas, espacios o guiones): cuántos
    teléfonos distintos lo han dado y el resultado de sus validaciones. Señal de suplantación."""
    wanted = _normalize_distributor_id(distributor_id)
    with _connection() as conn:
        rows = [
            dict(row) for row in conn.execute(
                "SELECT id, phone, distributor_id, discount_status, discount_pct, discount_resolved_at "
                "FROM orders WHERE distributor_id IS NOT NULL ORDER BY id"
            )
        ]
    uses = [r for r in rows if _normalize_distributor_id(r["distributor_id"]) == wanted and r["id"] != exclude_order_id]
    return {
        "usos_previos": len(uses),
        "telefonos_distintos": len({r["phone"][-10:] for r in uses}),
        "validaciones": [
            {"estado": r["discount_status"], "porcentaje": r["discount_pct"], "telefono": r["phone"]}
            for r in uses[-5:]
        ],
    }


# ---------------------------------------------------------------------------
# Alta de socios con pago (Ara) e identidad (Belén/Yeni)
# ---------------------------------------------------------------------------

ENROLLMENT_OPEN_STAGES = ("pago_pendiente", "pago_en_revision", "identidad_pendiente", "revision_identidad")


def create_enrollment_flow(origin: str, sponsor_phone: str = None, sponsor_id: str = None,
                           sponsor_name: str = None, prospect_phone: str = None, full_name: str = None) -> int:
    """Alta nueva del flujo con pago. `phone` es el del prospecto (vacío hasta que el
    patrocinador lo da en el flujo patrocinado)."""
    with _connection() as conn:
        return conn.execute(
            "INSERT INTO enrollments (phone, sponsor_distributor_number, sponsor_name, full_name, flow_stage, "
            "origin, sponsor_phone) VALUES (?, ?, ?, ?, 'pago_pendiente', ?, ?)",
            (prospect_phone or "", sponsor_id, sponsor_name, full_name, origin, sponsor_phone),
        ).lastrowid


def create_enrollment_payment_order(payer_phone: str, enrollment_id: int, product: str, amount: str) -> int:
    """Orden de pago de la cuota: entra directo a 'pendiente_pago' (no hay envío que cotizar)."""
    with _connection() as conn:
        order_id = conn.execute(
            "INSERT INTO orders (phone, product, amount_expected, status, kind, enrollment_id) "
            "VALUES (?, ?, ?, 'pendiente_pago', 'inscripcion', ?)",
            (payer_phone, product, amount, enrollment_id),
        ).lastrowid
        conn.execute("UPDATE enrollments SET order_id = ? WHERE id = ?", (order_id, enrollment_id))
        return order_id


def create_gift_order(phone: str, product: str, enrollment_id: int) -> int:
    """Pedido del regalo de bienvenida: producto en $0, el envío se cotiza como un pedido normal."""
    with _connection() as conn:
        return conn.execute(
            "INSERT INTO orders (phone, product, amount_expected, status, kind, enrollment_id) "
            "VALUES (?, ?, '$0', 'cotizando', 'regalo_inscripcion', ?)",
            (phone, product, enrollment_id),
        ).lastrowid


def set_enrollment_prospect(enrollment_id: int, phone: str, full_name: str) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE enrollments SET phone = ?, full_name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (phone, full_name, enrollment_id),
        )


def set_enrollment_stage(enrollment_id: int, stage: str, **fields) -> None:
    """Cambia la etapa y, si se dan, otros campos permitidos del alta."""
    allowed = {"status", "payment_approved_by", "ping_message_id", "ping_failed_notified", "approved_by",
               "rejection_reason", "ine_media_id", "face_media_id", "address", "sponsor_distributor_number",
               "sponsor_name", "sponsor_phone"}
    assigns, values = ["flow_stage = ?", "updated_at = CURRENT_TIMESTAMP"], [stage]
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"Campo no permitido: {key}")
        assigns.append(f"{key} = ?")
        values.append(value)
    with _connection() as conn:
        conn.execute(f"UPDATE enrollments SET {', '.join(assigns)} WHERE id = ?", (*values, enrollment_id))


def set_enrollment_ping(enrollment_id: int, message_id: str = None) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE enrollments SET ping_message_id = ?, ping_failed_notified = 0 WHERE id = ?",
            (message_id, enrollment_id),
        )


def mark_ping_failure_notified(enrollment_id: int) -> bool:
    """True solo la primera vez (para avisar al patrocinador una sola vez por ping)."""
    with _connection() as conn:
        return conn.execute(
            "UPDATE enrollments SET ping_failed_notified = 1 WHERE id = ? AND ping_failed_notified = 0",
            (enrollment_id,),
        ).rowcount > 0


def get_enrollment_by_order(order_id: int):
    with _connection() as conn:
        row = conn.execute("SELECT * FROM enrollments WHERE order_id = ?", (order_id,)).fetchone()
        return dict(row) if row else None


def get_identity_pending_enrollment(phone: str):
    """Alta del flujo nuevo que espera INE/selfie de este teléfono (comparado por 10 dígitos)."""
    last10 = (phone or "")[-10:]
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM enrollments WHERE flow_stage = 'identidad_pendiente' AND phone != '' "
            "AND substr(phone, -10) = ? ORDER BY id DESC LIMIT 1",
            (last10,),
        ).fetchone()
        return dict(row) if row else None


def get_open_enrollment_for_prospect(phone: str):
    """Alta en curso (cualquier etapa abierta) donde este teléfono es el prospecto."""
    last10 = (phone or "")[-10:]
    placeholders = ", ".join("?" for _ in ENROLLMENT_OPEN_STAGES)
    with _connection() as conn:
        row = conn.execute(
            f"SELECT * FROM enrollments WHERE flow_stage IN ({placeholders}) AND phone != '' "
            f"AND substr(phone, -10) = ? ORDER BY id DESC LIMIT 1",
            (*ENROLLMENT_OPEN_STAGES, last10),
        ).fetchone()
        return dict(row) if row else None


def get_open_enrollment_by_sponsor(sponsor_phone: str):
    placeholders = ", ".join("?" for _ in ENROLLMENT_OPEN_STAGES)
    with _connection() as conn:
        row = conn.execute(
            f"SELECT * FROM enrollments WHERE origin = 'patrocinador' AND sponsor_phone = ? "
            f"AND flow_stage IN ({placeholders}) ORDER BY id DESC LIMIT 1",
            (sponsor_phone, *ENROLLMENT_OPEN_STAGES),
        ).fetchone()
        return dict(row) if row else None


def get_enrollment_for_review(phone_digits: str):
    last10 = (phone_digits or "")[-10:]
    if len(last10) < 10:
        return None
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM enrollments WHERE flow_stage = 'revision_identidad' AND substr(phone, -10) = ? "
            "ORDER BY id DESC LIMIT 1",
            (last10,),
        ).fetchone()
        return dict(row) if row else None


def get_enrollment_by_ping(message_id: str):
    if not message_id:
        return None
    with _connection() as conn:
        row = conn.execute("SELECT * FROM enrollments WHERE ping_message_id = ?", (message_id,)).fetchone()
        return dict(row) if row else None


def list_enrollments_for_review() -> list:
    with _connection() as conn:
        rows = conn.execute(
            "SELECT * FROM enrollments WHERE flow_stage = 'revision_identidad' ORDER BY updated_at"
        ).fetchall()
        return [dict(row) for row in rows]


def activate_member(phone: str, enrollment_id: int, discount_pct: float, approved_by: str = None) -> None:
    """Socio activo: inscripción confirmada (cuenta en métricas) y descuento de por vida."""
    upsert_customer(phone)
    with _connection() as conn:
        conn.execute(
            "UPDATE enrollments SET flow_stage = 'activo', status = 'confirmado', approved_by = ?, "
            "activated_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (approved_by, enrollment_id),
        )
        conn.execute(
            "UPDATE customers SET member_since = CURRENT_TIMESTAMP, member_discount_pct = ?, "
            "member_enrollment_id = ?, updated_at = CURRENT_TIMESTAMP WHERE phone = ?",
            (discount_pct, enrollment_id, phone),
        )


def get_member(phone: str):
    """Socio activado por TEO (comparado por 10 dígitos), o None."""
    last10 = (phone or "")[-10:]
    if len(last10) < 10:
        return None
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM customers WHERE member_since IS NOT NULL AND substr(phone, -10) = ? LIMIT 1",
            (last10,),
        ).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Ficha verificada de socios y distribuidores (descuento automático y red)
# ---------------------------------------------------------------------------

def normalize_empresario_id(empresario_id: str) -> str:
    return _normalize_distributor_id(empresario_id)


def _digits10(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")[-10:]


def save_verified_profile(phone: str, *, kind: str, discount_pct: float = None, empresario_id: str = None,
                          name: str = None, scope: str = "general", notes: str = None, verified_by: str = None,
                          sponsor_empresario_id: str = None, sponsor_phone: str = None, address: str = None) -> None:
    """Guarda (o actualiza) la ficha de quien Belén/Yeni ya verificaron. Solo sobrescribe los campos
    que se dan, para no borrar el patrocinador o la dirección registrados antes."""
    upsert_customer(phone, name=name or None)
    fields = {
        "profile_kind": kind,
        "member_discount_pct": discount_pct,
        "empresario_id": (empresario_id or "").strip() or None,
        "discount_scope": scope if discount_pct is not None else None,
        "discount_notes": notes,
        "discount_verified_by": verified_by,
        "sponsor_empresario_id": (sponsor_empresario_id or "").strip() or None,
        "sponsor_phone": sponsor_phone,
        "address": address,
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    assigns = [f"{k} = ?" for k in fields] + ["discount_verified_at = CURRENT_TIMESTAMP", "updated_at = CURRENT_TIMESTAMP"]
    with _connection() as conn:
        conn.execute(f"UPDATE customers SET {', '.join(assigns)} WHERE phone = ?", (*fields.values(), phone))


def set_customer_empresario_id(phone: str, empresario_id: str) -> None:
    """ID que dio un patrocinador al inscribir; solo se guarda si su ficha aún no tiene uno."""
    upsert_customer(phone)
    with _connection() as conn:
        conn.execute(
            "UPDATE customers SET empresario_id = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE phone = ? AND (empresario_id IS NULL OR empresario_id = '')",
            ((empresario_id or "").strip(), phone),
        )


def get_verified_profile(phone: str):
    """Ficha con descuento verificado (socio activo o distribuidor validado), por 10 dígitos."""
    last10 = _digits10(phone)
    if len(last10) < 10:
        return None
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM customers WHERE member_discount_pct IS NOT NULL AND substr(phone, -10) = ? "
            "ORDER BY discount_verified_at DESC LIMIT 1",
            (last10,),
        ).fetchone()
        return dict(row) if row else None


def get_profile_by_empresario_id(empresario_id: str):
    wanted = _normalize_distributor_id(empresario_id)
    if not wanted:
        return None
    with _connection() as conn:
        rows = conn.execute("SELECT * FROM customers WHERE empresario_id IS NOT NULL AND empresario_id != ''").fetchall()
    matches = [dict(r) for r in rows if _normalize_distributor_id(r["empresario_id"]) == wanted]
    if not matches:
        return None
    # Si el mismo ID aparece en varios teléfonos, gana la ficha verificada más reciente.
    matches.sort(key=lambda r: (r.get("discount_verified_at") or "", r.get("updated_at") or ""), reverse=True)
    return matches[0]


def find_leader(identifier: str):
    """Líder por ID de empresario o por teléfono (10 dígitos)."""
    profile = get_profile_by_empresario_id(identifier)
    if profile:
        return profile
    last10 = _digits10(identifier)
    if len(last10) == 10:
        with _connection() as conn:
            row = conn.execute(
                "SELECT * FROM customers WHERE substr(phone, -10) = ? AND empresario_id IS NOT NULL "
                "AND empresario_id != '' LIMIT 1", (last10,),
            ).fetchone()
            return dict(row) if row else None
    return None


def set_commission_pct(phone: str, pct: float) -> None:
    with _connection() as conn:
        conn.execute("UPDATE customers SET commission_pct = ?, updated_at = CURRENT_TIMESTAMP WHERE phone = ?",
                     (pct, phone))


def list_direct_members(leader_empresario_id: str) -> list:
    wanted = _normalize_distributor_id(leader_empresario_id)
    with _connection() as conn:
        rows = conn.execute("SELECT * FROM customers WHERE sponsor_empresario_id IS NOT NULL").fetchall()
    return [dict(r) for r in rows if _normalize_distributor_id(r["sponsor_empresario_id"]) == wanted]


# ---------------------------------------------------------------------------
# Contabilidad de red y comisiones
# ---------------------------------------------------------------------------

def record_network_purchase(order_id: int, buyer_phone: str, buyer_name: str, buyer_empresario_id: str,
                            leader_empresario_id: str, product: str, amount_public: float, discount_pct: float,
                            amount_paid: float) -> bool:
    with _connection() as conn:
        return conn.execute(
            "INSERT OR IGNORE INTO network_purchases (order_id, buyer_phone, buyer_name, buyer_empresario_id, "
            "leader_empresario_id, product, amount_public, discount_pct, amount_paid) VALUES (?,?,?,?,?,?,?,?,?)",
            (order_id, buyer_phone, buyer_name, buyer_empresario_id, leader_empresario_id, product, amount_public,
             discount_pct, amount_paid),
        ).rowcount > 0


def list_network_purchases(start_utc: str, end_utc: str) -> list:
    with _connection() as conn:
        rows = conn.execute(
            "SELECT * FROM network_purchases WHERE created_at > ? AND created_at <= ? ORDER BY created_at, id",
            (start_utc, end_utc),
        ).fetchall()
        return [dict(r) for r in rows]


def create_commission_run(period_key: str, start_utc: str, end_utc: str, label: str, lines: list):
    """Crea el corte con sus líneas; None si ese periodo ya se generó (evita duplicados al reiniciar)."""
    with _connection() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO commission_runs (period_key, period_start_utc, period_end_utc, label) VALUES (?,?,?,?)",
                (period_key, start_utc, end_utc, label),
            )
        except sqlite3.IntegrityError:
            return None
        run_id = cur.lastrowid
        for position, line in enumerate(lines, start=1):
            conn.execute(
                "INSERT INTO commission_lines (run_id, position, leader_empresario_id, leader_phone, leader_name, "
                "purchases, volume, commission_pct, commission) VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, position, line["leader_empresario_id"], line.get("leader_phone"), line.get("leader_name"),
                 line["purchases"], line["volume"], line.get("commission_pct"), line.get("commission")),
            )
        return run_id


def get_commission_run(run_id: int):
    with _connection() as conn:
        row = conn.execute("SELECT * FROM commission_runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


def get_open_commission_run():
    """Corte más reciente que todavía no termina (revisión, corrección o entrega de comprobantes)."""
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM commission_runs WHERE status IN ('revision_control', 'correccion', 'validado') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def set_commission_run_status(run_id: int, status: str, validated_by: str = None, notes: str = None) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE commission_runs SET status = ?, validated_by = COALESCE(?, validated_by), "
            "validated_at = CASE WHEN ? = 'validado' THEN CURRENT_TIMESTAMP ELSE validated_at END, "
            "notes = COALESCE(?, notes) WHERE id = ?",
            (status, validated_by, status, notes, run_id),
        )


def list_commission_lines(run_id: int) -> list:
    with _connection() as conn:
        rows = conn.execute("SELECT * FROM commission_lines WHERE run_id = ? ORDER BY position", (run_id,)).fetchall()
        return [dict(r) for r in rows]


def get_commission_line(line_id: int):
    with _connection() as conn:
        row = conn.execute("SELECT * FROM commission_lines WHERE id = ?", (line_id,)).fetchone()
        return dict(row) if row else None


def update_commission_line(line_id: int, **fields) -> None:
    allowed = {"leader_phone", "leader_name", "purchases", "volume", "commission_pct", "commission", "adjusted",
               "proof_media_id", "delivery_status"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Campos no permitidos: {unknown}")
    assigns = [f"{k} = ?" for k in fields]
    if fields.get("delivery_status") == "entregado":
        assigns.append("delivered_at = CURRENT_TIMESTAMP")
    with _connection() as conn:
        conn.execute(f"UPDATE commission_lines SET {', '.join(assigns)} WHERE id = ?", (*fields.values(), line_id))


# ---------------------------------------------------------------------------
# Actividad entrante y entregas en espera (ventana de 24 h de WhatsApp)
# ---------------------------------------------------------------------------

def touch_inbound(phone: str) -> None:
    last10 = _digits10(phone)
    if len(last10) < 10:
        return
    with _connection() as conn:
        conn.execute(
            "INSERT INTO inbound_activity (phone_last10, last_at) VALUES (?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(phone_last10) DO UPDATE SET last_at = CURRENT_TIMESTAMP",
            (last10,),
        )


def hours_since_inbound(phone: str):
    """Horas desde el último mensaje que nos mandó este teléfono, o None si nunca escribió."""
    with _connection() as conn:
        row = conn.execute(
            "SELECT (julianday('now') - julianday(last_at)) * 24 AS hours FROM inbound_activity WHERE phone_last10 = ?",
            (_digits10(phone),),
        ).fetchone()
        return row["hours"] if row else None


def queue_delivery(phone: str, kind: str, ref_id: int) -> None:
    last10 = _digits10(phone)
    with _connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM pending_deliveries WHERE phone_last10 = ? AND kind = ? AND ref_id = ? AND delivered_at IS NULL",
            (last10, kind, ref_id),
        ).fetchone()
        if not exists:
            conn.execute("INSERT INTO pending_deliveries (phone_last10, kind, ref_id) VALUES (?, ?, ?)",
                         (last10, kind, ref_id))


def take_pending_deliveries(phone: str) -> list:
    """Entregas en espera para este teléfono; las marca como entregadas al tomarlas."""
    last10 = _digits10(phone)
    with _connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM pending_deliveries WHERE phone_last10 = ? AND delivered_at IS NULL ORDER BY id", (last10,),
        )]
        if rows:
            marks = ",".join("?" for _ in rows)
            conn.execute(f"UPDATE pending_deliveries SET delivered_at = CURRENT_TIMESTAMP WHERE id IN ({marks})",
                         [r["id"] for r in rows])
        return rows
