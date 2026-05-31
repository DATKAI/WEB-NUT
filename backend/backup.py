"""
NUT Monitor — Backup Module
Supports: local, FTP, SFTP, WebDAV (Yandex.Disk, Nextcloud), SMB
"""
import json
import os
import glob
from datetime import datetime

import db


BACKUP_DIR = "/opt/nut-monitor/backups"


def make_backup_data() -> dict:
    """Собирает данные для бэкапа"""
    settings = db.get_all_settings()
    return {
        "version": "1.0",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "settings": settings,
        "ups_devices": db.get_all_ups(),
        "panel_users": [
            {"username": u["username"], "role": u["role"]}
            for u in db.get_panel_users()
        ],
        "nut_users": db.get_nut_users(),
    }


def make_backup_json() -> tuple[str, bytes]:
    """Возвращает (filename, data)"""
    data = make_backup_data()
    filename = f"nut-monitor-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    return filename, content


def save_local(filename: str, content: bytes, path: str) -> str:
    """Сохранить локально. Возвращает полный путь."""
    os.makedirs(path, exist_ok=True)
    full = os.path.join(path, filename)
    with open(full, "wb") as f:
        f.write(content)
    return full


def cleanup_local(path: str, keep: int):
    """Удалить старые локальные бэкапы, оставить `keep` штук"""
    files = sorted(glob.glob(os.path.join(path, "nut-monitor-backup-*.json")))
    for old in files[:-keep]:
        try:
            os.remove(old)
        except Exception:
            pass


def send_ftp(filename: str, content: bytes, host: str, port: int,
             user: str, password: str, remote_path: str) -> None:
    import ftplib
    port = port or 21
    with ftplib.FTP() as ftp:
        ftp.connect(host, port, timeout=30)
        ftp.login(user, password)
        # Создаём папку если нет
        try:
            ftp.mkd(remote_path)
        except Exception:
            pass
        ftp.cwd(remote_path)
        import io
        ftp.storbinary(f"STOR {filename}", io.BytesIO(content))


def send_sftp(filename: str, content: bytes, host: str, port: int,
              user: str, password: str, remote_path: str) -> None:
    import paramiko
    port = port or 22
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, port=port, username=user, password=password, timeout=30)
    sftp = ssh.open_sftp()
    try:
        sftp.mkdir(remote_path)
    except Exception:
        pass
    import io
    sftp.putfo(io.BytesIO(content), f"{remote_path}/{filename}")
    sftp.close()
    ssh.close()


def send_webdav(filename: str, content: bytes, host: str,
                user: str, password: str, remote_path: str) -> None:
    """WebDAV: Яндекс.Диск, Nextcloud, ownCloud и др."""
    import urllib.request
    # Яндекс.Диск: https://webdav.yandex.ru
    # Nextcloud:   https://cloud.example.com/remote.php/dav/files/user
    base = host.rstrip("/")
    path = remote_path.strip("/")
    # Создаём папку
    mkcol_url = f"{base}/{path}"
    req = urllib.request.Request(mkcol_url, method="MKCOL")
    req.add_header("Authorization", _basic_auth(user, password))
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception:
        pass
    # Загружаем файл
    url = f"{base}/{path}/{filename}"
    req = urllib.request.Request(url, data=content, method="PUT")
    req.add_header("Authorization", _basic_auth(user, password))
    req.add_header("Content-Type", "application/json")
    urllib.request.urlopen(req, timeout=60)


def send_smb(filename: str, content: bytes, host: str, port: int,
             user: str, password: str, remote_path: str) -> None:
    """SMB/CIFS: сетевые папки Windows"""
    try:
        from smbprotocol.connection import Connection
        from smbprotocol.session import Session
        from smbprotocol.tree import TreeConnect
        from smbprotocol.open import Open, CreateDisposition, CreateOptions, FileAttributes, ShareAccess, ImpersonationLevel, FilePipePrinterAccessMask
        import uuid
    except ImportError:
        raise RuntimeError("Для SMB установите: pip install smbprotocol")

    port = port or 445
    # remote_path формат: //host/share/folder или просто /share/folder
    parts = remote_path.strip("/").split("/", 1)
    share = parts[0]
    folder = parts[1] if len(parts) > 1 else ""

    conn = Connection(uuid.uuid4(), host, port)
    conn.connect()
    session = Session(conn, user, password)
    session.connect()
    tree = TreeConnect(session, f"\\\\{host}\\{share}")
    tree.connect()

    import io
    file_open = Open(tree, f"{folder}\\{filename}" if folder else filename)
    file_open.create(
        ImpersonationLevel.Impersonation,
        FilePipePrinterAccessMask.GENERIC_WRITE,
        FileAttributes.FILE_ATTRIBUTE_NORMAL,
        ShareAccess.FILE_SHARE_READ,
        CreateDisposition.FILE_OVERWRITE_IF,
        CreateOptions.FILE_NON_DIRECTORY_FILE,
    )
    file_open.write(content, 0)
    file_open.close()
    tree.disconnect()
    session.disconnect()
    conn.disconnect()


def _basic_auth(user: str, password: str) -> str:
    import base64
    cred = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {cred}"


def run_backup(settings: dict = None) -> dict:
    """
    Выполнить бэкап согласно настройкам.
    Возвращает {"ok": bool, "filename": str, "destination": str, "error": str}
    """
    if settings is None:
        settings = db.get_all_settings()

    dest      = settings.get("backup_dest", "local")
    host      = settings.get("backup_host", "")
    user      = settings.get("backup_user", "")
    password  = settings.get("backup_pass", "")
    remote    = settings.get("backup_path", "/nut-monitor")
    keep      = int(settings.get("backup_keep", "7"))
    local_dir = settings.get("backup_local_path", BACKUP_DIR)

    try:
        port_str = settings.get("backup_port", "")
        port = int(port_str) if port_str else 0
    except Exception:
        port = 0

    filename, content = make_backup_json()
    size_kb = len(content) // 1024 or 1

    try:
        # Всегда сохраняем локально
        save_local(filename, content, local_dir)
        cleanup_local(local_dir, keep)

        # Отправляем на удалённое хранилище
        if dest == "ftp":
            send_ftp(filename, content, host, port, user, password, remote)
        elif dest == "sftp":
            send_sftp(filename, content, host, port, user, password, remote)
        elif dest == "webdav":
            send_webdav(filename, content, host, user, password, remote)
        elif dest == "smb":
            send_smb(filename, content, host, port, user, password, remote)
        # dest == "local" — уже сохранили

        db.log_backup(filename, size_kb, dest, "ok")
        db.log_event("system", "BACKUP", f"Бэкап создан: {filename} → {dest}")
        return {"ok": True, "filename": filename, "destination": dest}

    except Exception as e:
        err = str(e)
        db.log_backup(filename, size_kb, dest, "error", err)
        db.log_event("system", "BACKUP_ERR", f"Ошибка бэкапа ({dest}): {err}")
        return {"ok": False, "filename": filename, "destination": dest, "error": err}
