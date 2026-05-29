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


# ─────────── Скрипты для клиентов ───────────

def _get_slave_creds():
    users = db.get_nut_users()
    slave = next((u for u in users if u["role"] == "slave"), None)
    return (slave["username"], slave["password"]) if slave else ("upslave", "")

def _get_server_ip(request: Request):
    host = request.headers.get("host", "").split(":")[0]
    return host if host else "192.168.10.3"

def _get_ups_names():
    devices = db.get_ups_list()
    return [d["name"] for d in devices] or ["ippon"]

def _make_zip(*files) -> bytes:
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files:
            z.writestr(name, content)
    return buf.getvalue()


@app.get("/api/scripts/winnut-install.zip")
async def script_winnut_zip(request: Request):
    require_user(request)
    ip = _get_server_ip(request)
    user, password = _get_slave_creds()
    ups_list = _get_ups_names()
    ups = ups_list[0]

    script = f"""# WinNUT-Client Auto Installer
# Generated by NUT Monitor — {ip}
# Run as Administrator

$ErrorActionPreference = "Stop"
$ServerHost = "{ip}"
$ServerPort = 3493
$UpsName    = "{ups}"
$Login      = "{user}"
$Password   = "{password}"

Write-Host "=== WinNUT-Client Installer ===" -ForegroundColor Cyan

# Get latest release from GitHub
Write-Host "[1/4] Getting latest release..." -ForegroundColor Yellow
try {{
    $release = Invoke-RestMethod "https://api.github.com/repos/nutdotnet/WinNUT-Client/releases/latest"
    $asset = $release.assets | Where-Object {{ $_.name -like "*.exe" -or $_.name -like "*Setup*" }} | Select-Object -First 1
    if (-not $asset) {{
        $asset = $release.assets | Select-Object -First 1
    }}
    $downloadUrl = $asset.browser_download_url
    $fileName = $asset.name
    Write-Host "  Version: $($release.tag_name)"
    Write-Host "  File: $fileName"
}} catch {{
    Write-Host "  GitHub unavailable, using direct URL" -ForegroundColor Yellow
    $downloadUrl = "https://github.com/nutdotnet/WinNUT-Client/releases/latest/download/WinNUT-Client-Setup.exe"
    $fileName = "WinNUT-Client-Setup.exe"
}}

# Download
Write-Host "[2/4] Downloading..." -ForegroundColor Yellow
$tmpPath = "$env:TEMP\\$fileName"
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri $downloadUrl -OutFile $tmpPath -UseBasicParsing
Write-Host "  Saved to: $tmpPath"

# Install
Write-Host "[3/4] Installing..." -ForegroundColor Yellow
if ($fileName -like "*.exe") {{
    Start-Process -FilePath $tmpPath -ArgumentList "/S", "/silent", "/quiet" -Wait -NoNewWindow
}} elseif ($fileName -like "*.zip") {{
    $installDir = "$env:ProgramFiles\\WinNUT-Client"
    New-Item -ItemType Directory -Force -Path $installDir | Out-Null
    Expand-Archive -Path $tmpPath -DestinationPath $installDir -Force
}}

# Configure settings
Write-Host "[4/4] Configuring..." -ForegroundColor Yellow
$configPaths = @(
    "$env:APPDATA\\WinNUT-Client",
    "$env:LOCALAPPDATA\\WinNUT-Client",
    "$env:ProgramData\\WinNUT-Client"
)
foreach ($p in $configPaths) {{
    if (-not (Test-Path $p)) {{ New-Item -ItemType Directory -Force -Path $p | Out-Null }}
}}

# Registry settings
$regBase = "HKCU:\\Software\\WinNUT-Client"
if (-not (Test-Path $regBase)) {{ New-Item -Path $regBase -Force | Out-Null }}
Set-ItemProperty -Path $regBase -Name "Host"     -Value $ServerHost -Type String
Set-ItemProperty -Path $regBase -Name "Port"     -Value $ServerPort -Type DWord
Set-ItemProperty -Path $regBase -Name "UpsName"  -Value $UpsName   -Type String
Set-ItemProperty -Path $regBase -Name "Login"    -Value $Login     -Type String
Set-ItemProperty -Path $regBase -Name "Password" -Value $Password  -Type String

# App settings XML (some versions use this)
$settingsXml = @"
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <userSettings>
    <WinNUT_Client.Properties.Settings>
      <setting name="NUT_Host" serializeAs="String"><value>$ServerHost</value></setting>
      <setting name="NUT_Port" serializeAs="String"><value>$ServerPort</value></setting>
      <setting name="UPS_Name" serializeAs="String"><value>$UpsName</value></setting>
      <setting name="NUT_Login" serializeAs="String"><value>$Login</value></setting>
      <setting name="NUT_Password" serializeAs="String"><value>$Password</value></setting>
      <setting name="AutoConnect" serializeAs="String"><value>True</value></setting>
    </WinNUT_Client.Properties.Settings>
  </userSettings>
</configuration>
"@
foreach ($p in $configPaths) {{
    $settingsXml | Out-File "$p\\user.config" -Encoding UTF8 -Force
}}

Write-Host ""
Write-Host "Done! WinNUT-Client installed and configured." -ForegroundColor Green
Write-Host "Server: ${ServerHost}:$ServerPort  UPS: $UpsName" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press any key to exit..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
"""
    bat = f"""@echo off
:: WinNUT-Client Auto Installer — NUT Server: {ip}
:: Run as Administrator

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator rights...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0winnut-install.ps1"
echo.
echo Press any key to exit...
pause >nul
"""
    from fastapi.responses import Response as FR
    data = _make_zip(("winnut-install.ps1", script), ("winnut-install.bat", bat))
    return FR(content=data, media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=winnut-install.zip"})


@app.get("/api/scripts/winnut-install.bat")
async def script_winnut_bat(request: Request):
    require_user(request)
    ip = _get_server_ip(request)
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(f"@echo off\npowershell -ExecutionPolicy Bypass -File winnut-install.ps1\npause", media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=winnut-install.bat"})


@app.get("/api/scripts/nut-monitor.ps1")
async def script_monitor_ps(request: Request):
    require_user(request)
    ip = _get_server_ip(request)
    user, password = _get_slave_creds()
    ups_list = _get_ups_names()
    ups = ups_list[0]

    script = f"""# NUT Monitor Client — PowerShell Service
# Generated by NUT Monitor — {ip}
# Installs as Windows Scheduled Task for auto-start

param([switch]$Install, [switch]$Uninstall, [switch]$Run)

$ServerHost = "{ip}"
$ServerPort  = 3493
$UpsName     = "{ups}"
$Login       = "{user}"
$Password    = "{password}"
$TaskName    = "NUT-Monitor-Client"
$LogFile     = "$env:ProgramData\\NUT-Monitor\\nut-monitor.log"
$ScriptDest  = "$env:ProgramData\\NUT-Monitor\\nut-monitor.ps1"

function Write-Log($msg) {{
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts  $msg" | Tee-Object -FilePath $LogFile -Append | Write-Host
}}

function Get-UpsVar($varName) {{
    try {{
        $tcp = [System.Net.Sockets.TcpClient]::new($ServerHost, $ServerPort)
        $tcp.ReceiveTimeout = 5000; $tcp.SendTimeout = 5000
        $stream = $tcp.GetStream()
        $w = [System.IO.StreamWriter]::new($stream); $w.AutoFlush = $true
        $r = [System.IO.StreamReader]::new($stream)
        $w.WriteLine("USERNAME $Login"); $r.ReadLine() | Out-Null
        $w.WriteLine("PASSWORD $Password"); $r.ReadLine() | Out-Null
        $w.WriteLine("GET VAR $UpsName $varName")
        $resp = $r.ReadLine()
        $tcp.Close()
        if ($resp -match 'VAR .+ .+ "(.+)"') {{ return $Matches[1] }}
        return $null
    }} catch {{ return $null }}
}}

function Install-Task {{
    New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
    Copy-Item -Path $PSCommandPath -Destination $ScriptDest -Force
    $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptDest`" -Run"
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "NUT Monitor installed and started as scheduled task." -ForegroundColor Green
    Write-Host "Log: $LogFile"
}}

function Uninstall-Task {{
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "NUT Monitor task removed." -ForegroundColor Yellow
}}

function Start-Monitor {{
    New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
    Write-Log "NUT Monitor started. Server: $ServerHost UPS: $UpsName"
    $prevStatus = ""
    while ($true) {{
        $status = Get-UpsVar "ups.status"
        if ($null -eq $status) {{
            Write-Log "Cannot reach NUT server $ServerHost"
            Start-Sleep -Seconds 30
            continue
        }}
        if ($status -ne $prevStatus) {{
            Write-Log "Status changed: $prevStatus -> $status"
            $prevStatus = $status
        }}
        if ($status -like "*LB*") {{
            $charge = Get-UpsVar "battery.charge"
            Write-Log "LOW BATTERY ($charge%). Initiating shutdown in 30 seconds!"
            Start-Sleep -Seconds 30
            Write-Log "Shutting down NOW"
            Stop-Computer -Force
            exit
        }}
        Start-Sleep -Seconds 30
    }}
}}

if ($Install)   {{ Install-Task;   exit }}
if ($Uninstall) {{ Uninstall-Task; exit }}
if ($Run)       {{ Start-Monitor;  exit }}

# Interactive menu
Write-Host "NUT Monitor Client" -ForegroundColor Cyan
Write-Host "Server: $ServerHost   UPS: $UpsName"
Write-Host ""
Write-Host "[1] Install as Windows service (recommended)"
Write-Host "[2] Run manually (for testing)"
Write-Host "[3] Uninstall"
Write-Host "[Q] Quit"
$choice = Read-Host "Choose"
switch ($choice) {{
    "1" {{ Install-Task }}
    "2" {{ Start-Monitor }}
    "3" {{ Uninstall-Task }}
}}
"""
    from fastapi.responses import PlainTextResponse
    bat = f"""@echo off
:: NUT Monitor Client Launcher — NUT Server: {ip}
:: Run as Administrator to install as service

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator rights...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0nut-monitor.ps1"
pause
"""
    from fastapi.responses import Response as FR
    data = _make_zip(("nut-monitor.ps1", script), ("nut-monitor.bat", bat))
    return FR(content=data, media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=nut-monitor.zip"})


@app.get("/api/scripts/nut-monitor.bat")
async def script_monitor_bat(request: Request):
    require_user(request)
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("@echo off\npowershell -ExecutionPolicy Bypass -File nut-monitor.ps1\npause", media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=nut-monitor.bat"})


@app.get("/api/scripts/nut-service-install.zip")
async def script_service_zip(request: Request):
    require_user(request)
    ip   = _get_server_ip(request)
    user, password = _get_slave_creds()
    ups  = (_get_ups_names() or ["ups"])[0]

    # ── nut-monitor.ps1 — the actual monitor, baked credentials, no here-string ──
    monitor_lines = [
        "# NUT Monitor — auto-generated, do not edit",
        f'$Server   = "{ip}"',
        "$Port     = 3493",
        f'$UPS      = "{ups}"',
        f'$Login    = "{user}"',
        f'$Password = "{password}"',
        '$Log      = "C:\\NUT-Monitor\\nut-monitor.log"',
        "",
        "New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null",
        "",
        "function Write-Log($msg) {",
        "    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'",
        '    Add-Content -Path $Log -Value "$ts  $msg"',
        "}",
        "",
        "function Get-UpsVar($varName) {",
        "    try {",
        "        $tcp = [Net.Sockets.TcpClient]::new($Server, $Port)",
        "        $tcp.ReceiveTimeout = 5000; $tcp.SendTimeout = 5000",
        "        $s = $tcp.GetStream()",
        "        $w = [IO.StreamWriter]::new($s); $w.AutoFlush = $true",
        "        $r = [IO.StreamReader]::new($s)",
        '        $w.WriteLine("USERNAME $Login");    $r.ReadLine() | Out-Null',
        '        $w.WriteLine("PASSWORD $Password"); $r.ReadLine() | Out-Null',
        '        $w.WriteLine("GET VAR $UPS $varName")',
        "        $resp = $r.ReadLine()",
        "        $tcp.Close()",
        '        if ($resp -match \'VAR .+ .+ "(.+)"\') { return $Matches[1] }',
        "    } catch { }",
        "    return $null",
        "}",
        "",
        'Write-Log "=== NUT Monitor started. Server: $Server  UPS: $UPS ==="',
        '$prevStatus = ""',
        "",
        "while ($true) {",
        '    $status = Get-UpsVar "ups.status"',
        "    if ($null -eq $status) {",
        '        Write-Log "WARN: Cannot reach $Server:$Port"',
        "        Start-Sleep -Seconds 30",
        "        continue",
        "    }",
        "    if ($status -ne $prevStatus) {",
        '        $charge = Get-UpsVar "battery.charge"',
        '        Write-Log "Status: $prevStatus -> $status  (battery: $charge%)"',
        "        $prevStatus = $status",
        "    }",
        '    if ($status -like "*LB*") {',
        '        Write-Log "!!! LOW BATTERY - shutdown in 30 sec !!!"',
        "        Start-Sleep -Seconds 30",
        '        Write-Log "Shutdown NOW"',
        "        Stop-Computer -Force",
        "        exit",
        "    }",
        "    Start-Sleep -Seconds 30",
        "}",
    ]
    monitor = "\r\n".join(monitor_lines)

    # ── nut-service-install.ps1 — installer, copies monitor.ps1 from same folder ──
    installer_lines = [
        "# NUT Service Installer — Run as Administrator",
        "param([switch]$Uninstall)",
        "",
        '$ServiceName = "NUT-Monitor"',
        '$InstallDir  = "C:\\NUT-Monitor"',
        '$ScriptPath  = "$InstallDir\\nut-monitor.ps1"',
        '$WinswExe   = "$InstallDir\\NUT-Monitor.exe"',
        '$WinswXml   = "$InstallDir\\NUT-Monitor.xml"',
        '$LogPath     = "$InstallDir\\nut-monitor.log"',
        '$SrcScript   = Join-Path $PSScriptRoot "nut-monitor.ps1"',
        "",
        "if ($Uninstall) {",
        '    Write-Host "Removing NUT-Monitor service..." -ForegroundColor Yellow',
        "    if (Test-Path $WinswExe) {",
        "        & $WinswExe stop    2>$null",
        "        & $WinswExe uninstall 2>$null",
        "    } else {",
        '        Stop-Service $ServiceName -Force -ErrorAction SilentlyContinue',
        '        & sc.exe delete $ServiceName',
        "    }",
        '    Write-Host "Done." -ForegroundColor Green',
        '    Read-Host "Press Enter to exit"',
        "    exit",
        "}",
        "",
        'Write-Host "=== NUT Monitor Service Installer ===" -ForegroundColor Cyan',
        f'Write-Host "Server: {ip}:3493   UPS: {ups}"',
        'Write-Host ""',
        "",
        "# 1. Create folder",
        'Write-Host "[1/5] Creating $InstallDir..." -ForegroundColor Yellow',
        "New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null",
        "",
        "# 2. Download WinSW (service wrapper, hosted on GitHub)",
        'Write-Host "[2/5] Downloading WinSW service wrapper..." -ForegroundColor Yellow',
        "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12",
        "try {",
        '    Invoke-WebRequest "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe" -OutFile $WinswExe -UseBasicParsing',
        '    Write-Host "  OK: $WinswExe"',
        "} catch {",
        '    Write-Host "  ERROR downloading WinSW: $_" -ForegroundColor Red',
        '    Read-Host "Press Enter to exit"; exit 1',
        "}",
        "",
        "# 3. Copy monitor script",
        'Write-Host "[3/5] Copying monitor script..." -ForegroundColor Yellow',
        "if (-not (Test-Path $SrcScript)) {",
        '    Write-Host "  ERROR: nut-monitor.ps1 not found next to installer!" -ForegroundColor Red',
        '    Read-Host "Press Enter to exit"; exit 1',
        "}",
        "Copy-Item -Path $SrcScript -Destination $ScriptPath -Force",
        '    Write-Host "  OK: $ScriptPath"',
        "",
        "# 4. Write WinSW XML config",
        'Write-Host "[4/5] Registering service..." -ForegroundColor Yellow',
        '$xml = @"',
        '<service>',
        '  <id>NUT-Monitor</id>',
        '  <name>NUT UPS Monitor</name>',
        '  <description>NUT UPS monitor. Auto-shutdown on low battery.</description>',
        '  <executable>powershell.exe</executable>',
        '  <arguments>-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"</arguments>',
        '  <log mode="none"/>',
        '  <onfailure action="restart" delay="5 sec"/>',
        '  <onfailure action="restart" delay="10 sec"/>',
        '</service>',
        '"@',
        '$xml = $xml -f $ScriptPath',
        '$xml | Out-File -FilePath $WinswXml -Encoding UTF8 -Force',
        "$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue",
        "if ($existing) {",
        "    & $WinswExe stop      2>$null | Out-Null",
        "    & $WinswExe uninstall 2>$null | Out-Null",
        "    Start-Sleep 2",
        "}",
        "& $WinswExe install",
        "",
        "# 5. Start",
        'Write-Host "[5/5] Starting service..." -ForegroundColor Yellow',
        "& $WinswExe start",
        "Start-Sleep 3",
        "$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue",
        'if ($svc -and $svc.Status -eq "Running") {',
        '    Write-Host "  Service is RUNNING" -ForegroundColor Green',
        "} else {",
        '    Write-Host "  Service status: $($svc.Status)" -ForegroundColor Red',
        '    Write-Host "  Check log: $LogPath"',
        "}",
        "",
        'Write-Host ""',
        'Write-Host "=== Done! ===" -ForegroundColor Green',
        f'Write-Host "Service NUT-Monitor installed as SYSTEM, auto-start on boot."',
        f'Write-Host "Log: C:\\NUT-Monitor\\nut-monitor.log"',
        'Write-Host "To uninstall: run this script with -Uninstall"',
        'Write-Host ""',
        'Read-Host "Press Enter to exit"',
    ]
    ps1 = "\r\n".join(installer_lines)

    bat = (
        "@echo off\r\n"
        f":: NUT Service Installer — {ip}\r\n"
        "\r\n"
        "net session >nul 2>&1\r\n"
        "if %errorlevel% neq 0 (\r\n"
        "    echo Requesting administrator rights...\r\n"
        "    powershell -Command \"Start-Process '%~f0' -Verb RunAs\"\r\n"
        "    exit /b\r\n"
        ")\r\n"
        "\r\n"
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"%~dp0nut-service-install.ps1\"\r\n"
        "if %errorlevel% neq 0 (\r\n"
        "    echo.\r\n"
        "    echo ERROR: Script failed.\r\n"
        "    pause\r\n"
        ")\r\n"
    )

    from fastapi.responses import Response as FR
    data = _make_zip(
        ("nut-monitor.ps1",         monitor),
        ("nut-service-install.ps1", ps1),
        ("nut-service-install.bat", bat),
    )
    return FR(content=data, media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=nut-service-install.zip"})


# ─────────── Клиенты ───────────

@app.get("/api/clients")
async def api_clients(request: Request):
    require_user(request)
    try:
        result = subprocess.run(
            ["ss", "-tnp"],
            capture_output=True, text=True, timeout=5
        )
        seen = set()
        clients = []
        for line in result.stdout.splitlines():
            if ":3493" not in line or "ESTAB" not in line:
                continue
            parts = line.split()
            peer = parts[4] if len(parts) > 4 else ""
            ip = peer.rsplit(":", 1)[0] if ":" in peer else peer
            # Пропускаем localhost — это внутренние соединения NUT драйверов и upsmon
            if not ip or ip in ("", "0.0.0.0", "127.0.0.1", "::1", "localhost"):
                continue
            if ip not in seen:
                seen.add(ip)
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
