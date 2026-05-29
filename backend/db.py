import sqlite3
import hashlib
import secrets
from datetime import datetime

DB_PATH = "/opt/nut-monitor/data.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ups_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            vendorid TEXT,
            productid TEXT,
            serial TEXT,
            driver TEXT DEFAULT 'usbhid-ups',
            port TEXT DEFAULT 'auto',
            enabled INTEGER DEFAULT 1,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'viewer',
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS nut_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'slave',
            actions TEXT DEFAULT '',
            instcmds TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            ups TEXT,
            status TEXT,
            message TEXT
        );

        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            ups TEXT,
            charge REAL,
            load REAL,
            input_voltage REAL,
            runtime INTEGER
        );

        CREATE TABLE IF NOT EXISTS monitored_clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            hostname TEXT,
            ups TEXT,
            status TEXT,
            battery INTEGER,
            last_seen TEXT,
            UNIQUE(ip)
        );
    """)
    conn.commit()

    # Дефолтные настройки
    defaults = {
        "tg_token": "",
        "tg_chat_id": "",
        "smtp_host": "",
        "smtp_port": "587",
        "smtp_user": "",
        "smtp_password": "",
        "smtp_to": "",
        "ntfy_url": "",
        "notify_onbatt": "1",
        "notify_lowbatt": "1",
        "notify_online": "1",
        "low_batt_threshold": "30",
        "shutdown_delay": "60",
        "poll_interval": "3",
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    # Дефолтный admin если нет пользователей
    existing = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if existing == 0:
        pw = hash_password("admin")
        conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            ("admin", pw, "admin", now())
        )

    conn.commit()
    conn.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{salt}:{h}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, h = stored.split(":")
        return hashlib.sha256(f"{salt}{password}".encode()).hexdigest() == h
    except Exception:
        return False


# --- UPS устройства ---
def get_ups_list():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM ups_devices WHERE enabled=1").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_ups():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM ups_devices").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_ups(name, description, vendorid, productid, serial, driver="usbhid-ups", port="auto"):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO ups_devices (name, description, vendorid, productid, serial, driver, port, enabled, created_at) VALUES (?,?,?,?,?,?,?,1,?)",
        (name, description, vendorid, productid, serial, driver, port, now())
    )
    conn.commit()
    conn.close()


def delete_ups(name):
    conn = get_conn()
    conn.execute("DELETE FROM ups_devices WHERE name=?", (name,))
    conn.commit()
    conn.close()


def toggle_ups(name, enabled):
    conn = get_conn()
    conn.execute("UPDATE ups_devices SET enabled=? WHERE name=?", (enabled, name))
    conn.commit()
    conn.close()


# --- Настройки ---
def get_setting(key, default=""):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def set_setting(key, value):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    conn.commit()
    conn.close()


def get_all_settings():
    conn = get_conn()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


# --- Пользователи панели ---
def get_panel_users():
    conn = get_conn()
    rows = conn.execute("SELECT id, username, role, created_at FROM users").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_panel_user(username, password, role="viewer"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
        (username, hash_password(password), role, now())
    )
    conn.commit()
    conn.close()


def delete_panel_user(user_id):
    conn = get_conn()
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()


def authenticate_user(username, password):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if row and verify_password(password, row["password_hash"]):
        return dict(row)
    return None


# --- NUT пользователи ---
def get_nut_users():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM nut_users").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_nut_user(username, password, role, actions="", instcmds=""):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO nut_users (username, password, role, actions, instcmds) VALUES (?,?,?,?,?)",
        (username, password, role, actions, instcmds)
    )
    conn.commit()
    conn.close()


def delete_nut_user(username):
    conn = get_conn()
    conn.execute("DELETE FROM nut_users WHERE username=?", (username,))
    conn.commit()
    conn.close()


# --- Мониторинг клиентов ---
def upsert_client(ip, hostname, ups, status, battery):
    conn = get_conn()
    conn.execute(
        """INSERT INTO monitored_clients (ip, hostname, ups, status, battery, last_seen)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(ip) DO UPDATE SET
               hostname=excluded.hostname, ups=excluded.ups,
               status=excluded.status, battery=excluded.battery,
               last_seen=excluded.last_seen""",
        (ip, hostname, ups, status, battery, now())
    )
    conn.commit()
    conn.close()


def get_monitored_clients(timeout_minutes=5):
    conn = get_conn()
    rows = conn.execute(
        """SELECT ip, hostname, ups, status, battery, last_seen
           FROM monitored_clients
           WHERE datetime(last_seen) >= datetime('now', ?, 'localtime')
           ORDER BY last_seen DESC""",
        (f"-{timeout_minutes} minutes",)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- События ---
def log_event(ups, status, message):
    conn = get_conn()
    conn.execute(
        "INSERT INTO events (ts, ups, status, message) VALUES (?,?,?,?)",
        (now(), ups, status, message)
    )
    conn.commit()
    conn.close()


def get_events(limit=50):
    conn = get_conn()
    rows = conn.execute(
        "SELECT ts, ups, status, message FROM events ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Метрики ---
def save_metric(ups, charge, load, input_voltage, runtime):
    conn = get_conn()
    conn.execute(
        "INSERT INTO metrics (ts, ups, charge, load, input_voltage, runtime) VALUES (?,?,?,?,?,?)",
        (now(), ups, charge, load, input_voltage, runtime)
    )
    conn.commit()
    conn.close()


def get_metrics(ups, limit=60):
    conn = get_conn()
    rows = conn.execute(
        "SELECT ts, charge, load, input_voltage, runtime FROM metrics WHERE ups=? ORDER BY id DESC LIMIT ?",
        (ups, limit)
    ).fetchall()
    conn.close()
    return list(reversed([dict(r) for r in rows]))
