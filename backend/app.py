import asyncio
import json
import subprocess
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel
import uvicorn

import db
import nut
import notify
import auth

app = FastAPI(title="NUT Monitor")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Auth middleware ---
def get_user(request: Request):
    token = request.cookies.get("session")
    return auth.get_session(token)

def require_user(request: Request):
    user = get_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user

def require_admin(request: Request):
    user = require_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    return user

# --- WebSocket manager ---
class WsManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

ws_manager = WsManager()

# --- Отслеживание статусов ---
prev_status: dict = {}

async def poll_loop():
    while True:
        try:
            interval = int(db.get_setting("poll_interval", "3"))
            ups_list = db.get_ups_list()
            settings = db.get_all_settings()
            all_info = {}

            for device in ups_list:
                name = device["name"]
                info = nut.get_ups_info(name)
                all_info[name] = info

                # Сохраняем метрики
                if info["online"]:
                    db.save_metric(
                        name,
                        info.get("charge"),
                        info.get("load"),
                        info.get("input_voltage"),
                        info.get("runtime"),
                    )

                # Проверяем смену статуса
                st = info.get("status", "UNKNOWN")
                prev = prev_status.get(name)
                if prev and prev != st:
                    msg = f"Статус изменён: {prev} → {st}"
                    db.log_event(name, st, msg)

                    should_notify = False
                    if "OB" in st and settings.get("notify_onbatt") == "1":
                        should_notify = True
                        msg = f"⚠️ Переход на батарею! Статус: {st}"
                    elif "LB" in st and settings.get("notify_lowbatt") == "1":
                        should_notify = True
                        msg = f"🔴 Низкий заряд батареи! Статус: {st}"
                    elif "OL" in st and prev and "OB" in prev and settings.get("notify_online") == "1":
                        should_notify = True
                        msg = f"✅ Питание восстановлено. Статус: {st}"

                    if should_notify:
                        notify.notify_all(settings, name, st, msg)

                prev_status[name] = st

            events = db.get_events(20)
            nut_status = nut.get_nut_status()
            await ws_manager.broadcast({
                "ups": all_info,
                "events": events,
                "nut_status": nut_status,
            })
        except Exception as e:
            print(f"[poll] Ошибка: {e}")

        await asyncio.sleep(interval)


@app.on_event("startup")
async def startup():
    db.init_db()
    asyncio.create_task(poll_loop())


# ─────────── Страницы ───────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = get_user(request)
    if not user:
        return FileResponse("static/login.html")
    return FileResponse("static/index.html")

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return FileResponse("static/login.html")


# ─────────── Auth API ───────────

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    user = db.authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token = auth.create_session(user)
    resp = JSONResponse({"ok": True, "role": user["role"], "username": user["username"]})
    resp.set_cookie("session", token, httponly=True, max_age=86400, samesite="lax")
    return resp

@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("session")
    auth.delete_session(token)
    response.delete_cookie("session")
    return {"ok": True}

@app.get("/api/auth/me")
async def me(request: Request):
    user = get_user(request)
    if not user:
        raise HTTPException(status_code=401)
    return {"username": user["username"], "role": user["role"]}


# ─────────── UPS API ───────────

@app.get("/api/ups")
async def api_ups(request: Request):
    require_user(request)
    ups_list = db.get_ups_list()
    result = {}
    for device in ups_list:
        result[device["name"]] = nut.get_ups_info(device["name"])
    return result

@app.get("/api/ups/all")
async def api_ups_all(request: Request):
    require_admin(request)
    return db.get_all_ups()

class UpsDevice(BaseModel):
    name: str
    description: str = ""
    vendorid: str = ""
    productid: str = ""
    serial: str = ""
    driver: str = "usbhid-ups"
    port: str = "auto"

@app.post("/api/ups/add")
async def api_ups_add(device: UpsDevice, request: Request):
    require_admin(request)
    db.add_ups(device.name, device.description, device.vendorid,
               device.productid, device.serial, device.driver, device.port)
    _apply_nut_config()
    return {"ok": True}

@app.delete("/api/ups/{name}")
async def api_ups_delete(name: str, request: Request):
    require_admin(request)
    db.delete_ups(name)
    _apply_nut_config()
    return {"ok": True}

@app.post("/api/ups/{name}/toggle")
async def api_ups_toggle(name: str, request: Request):
    body = await request.json()
    require_admin(request)
    db.toggle_ups(name, body.get("enabled", 1))
    return {"ok": True}

@app.get("/api/ups/scan")
async def api_ups_scan(request: Request):
    require_admin(request)
    return nut.scan_usb()

@app.get("/api/ups/{name}/metrics")
async def api_metrics(name: str, request: Request):
    require_user(request)
    return db.get_metrics(name, 120)

@app.get("/api/ups/{name}/commands")
async def api_commands(name: str, request: Request):
    require_user(request)
    s = db.get_all_settings()
    return nut.list_commands(name, s.get("nut_admin_user", "admin"), s.get("nut_admin_pass", ""))

@app.post("/api/ups/{name}/command/{command}")
async def api_command(name: str, command: str, request: Request):
    require_user(request)
    s = db.get_all_settings()
    result = nut.run_command(name, command, s.get("nut_admin_user", "admin"), s.get("nut_admin_pass", ""))
    db.log_event(name, "CMD", f"Команда: {command} → {result.get('result', result.get('error', ''))}")
    return result


# ─────────── NUT управление ───────────

@app.get("/api/nut/status")
async def api_nut_status(request: Request):
    require_user(request)
    return nut.get_nut_status()

@app.post("/api/nut/restart")
async def api_nut_restart(request: Request):
    require_admin(request)
    result = nut.restart_nut()
    db.log_event("system", "RESTART", "NUT перезапущен через панель")
    return result

@app.post("/api/nut/apply")
async def api_nut_apply(request: Request):
    require_admin(request)
    _apply_nut_config()
    return nut.restart_nut()


# ─────────── Настройки ───────────

@app.get("/api/settings")
async def api_settings_get(request: Request):
    require_admin(request)
    s = db.get_all_settings()
    # Скрываем пароли
    for k in ("smtp_password", "tg_token", "nut_admin_pass"):
        if k in s and s[k]:
            s[k] = "••••••••"
    return s

@app.post("/api/settings")
async def api_settings_set(request: Request):
    require_admin(request)
    body = await request.json()
    for k, v in body.items():
        if v != "••••••••":
            db.set_setting(k, v)
    return {"ok": True}

@app.post("/api/settings/test-telegram")
async def api_test_tg(request: Request):
    require_admin(request)
    token = db.get_setting("tg_token")
    chat_id = db.get_setting("tg_chat_id")
    ok = notify.test_telegram(token, chat_id)
    return {"ok": ok}

@app.post("/api/settings/test-email")
async def api_test_email(request: Request):
    require_admin(request)
    s = db.get_all_settings()
    ok = notify.test_email(s["smtp_host"], s["smtp_port"], s["smtp_user"],
                           s["smtp_password"], s["smtp_to"])
    return {"ok": ok}


# ─────────── Пользователи панели ───────────

@app.get("/api/users")
async def api_users(request: Request):
    require_admin(request)
    return db.get_panel_users()

class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "viewer"

@app.post("/api/users")
async def api_users_add(user: UserCreate, request: Request):
    require_admin(request)
    db.add_panel_user(user.username, user.password, user.role)
    return {"ok": True}

@app.delete("/api/users/{user_id}")
async def api_users_delete(user_id: int, request: Request):
    require_admin(request)
    db.delete_panel_user(user_id)
    return {"ok": True}


# ─────────── NUT пользователи ───────────

@app.get("/api/nut-users")
async def api_nut_users(request: Request):
    require_admin(request)
    return db.get_nut_users()

class NutUser(BaseModel):
    username: str
    password: str
    role: str = "slave"
    actions: str = ""
    instcmds: str = ""

@app.post("/api/nut-users")
async def api_nut_users_add(user: NutUser, request: Request):
    require_admin(request)
    db.add_nut_user(user.username, user.password, user.role, user.actions, user.instcmds)
    _apply_nut_config()
    return {"ok": True}

@app.delete("/api/nut-users/{username}")
async def api_nut_users_delete(username: str, request: Request):
    require_admin(request)
    db.delete_nut_user(username)
    _apply_nut_config()
    return {"ok": True}


# ─────────── Клиенты ───────────

@app.get("/api/clients")
async def api_clients(request: Request):
    require_user(request)
    try:
        result = subprocess.run(
            ["ss", "-tnp"],
            capture_output=True, text=True, timeout=5
        )
        clients = []
        for line in result.stdout.splitlines():
            if ":3493" in line and "ESTAB" in line:
                parts = line.split()
                # Формат: State Recv-Q Send-Q Local Peer
                peer = parts[4] if len(parts) > 4 else ""
                ip = peer.rsplit(":", 1)[0] if ":" in peer else peer
                if ip and ip not in ("", "0.0.0.0"):
                    clients.append({"ip": ip, "state": "connected"})
        return clients
    except Exception as e:
        return []

# ─────────── События ───────────

@app.get("/api/events")
async def api_events(request: Request):
    require_user(request)
    return db.get_events(100)


# ─────────── WebSocket ───────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    # Проверяем сессию через cookie
    token = ws.cookies.get("session")
    user = auth.get_session(token)
    if not user:
        await ws.close(code=4001)
        return
    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


# ─────────── Внутренние утилиты ───────────

def _apply_nut_config():
    ups_list = db.get_all_ups()
    enabled = [u for u in ups_list if u.get("enabled")]
    nut.write_ups_conf(enabled)
    nut_users = db.get_nut_users()
    nut.write_upsd_users(nut_users)


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
