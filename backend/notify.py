import smtplib
import urllib.request
import urllib.parse
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_telegram(token: str, chat_id: str, message: str) -> bool:
    if not token or not chat_id:
        return False
    try:
        text = urllib.parse.quote(message)
        url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={text}&parse_mode=HTML"
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f"[Telegram] Ошибка: {e}")
        return False


def send_ntfy(url: str, message: str, title: str = "NUT Monitor", priority: str = "default") -> bool:
    """Отправка уведомления через ntfy.sh — с повторными попытками в фоне"""
    if not url:
        return False

    import subprocess, shutil, threading, time

    curl_bin = shutil.which("curl") or "/usr/bin/curl"
    cmd = [curl_bin, "-sfL", "--max-time", "15",
           "-X", "POST", url,
           "-H", f"Title: {title}",
           "-H", f"Priority: {priority}",
           "-H", "Tags: zap",
           "-H", "Content-Type: text/plain; charset=utf-8",
           "-d", message]

    def _send_with_retry():
        for attempt in range(1, 4):  # 3 попытки
            try:
                result = subprocess.run(cmd, capture_output=True, timeout=20)
                if result.returncode == 0:
                    print(f"[ntfy] OK (попытка {attempt})")
                    return
                print(f"[ntfy] попытка {attempt} неудачна: {result.stderr.decode()[:100]}")
            except Exception as e:
                print(f"[ntfy] попытка {attempt} ошибка: {e}")
            if attempt < 3:
                time.sleep(5)  # пауза 5 сек перед следующей попыткой
        print("[ntfy] все попытки исчерпаны")

    # Запускаем в фоновом потоке — не блокируем основной цикл
    threading.Thread(target=_send_with_retry, daemon=True).start()
    return True


def send_email(host: str, port: int, user: str, password: str, to: str, subject: str, body: str) -> bool:
    if not host or not user or not to:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = to
        msg.attach(MIMEText(body, "html", "utf-8"))
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.ehlo()
            s.starttls()
            s.login(user, password)
            s.sendmail(user, to, msg.as_string())
        return True
    except Exception as e:
        print(f"[Email] Ошибка: {e}")
        return False


def notify_all(settings: dict, ups_name: str, status: str, message: str):
    subject = f"[NUT Monitor] {ups_name} — {status}"
    body = f"""
    <div style="font-family:sans-serif;padding:20px;background:#f0f0f0">
      <h2 style="color:#c00">⚡ NUT Monitor Alert</h2>
      <table style="background:#fff;padding:16px;border-radius:8px">
        <tr><td><b>UPS:</b></td><td>{ups_name}</td></tr>
        <tr><td><b>Статус:</b></td><td>{status}</td></tr>
        <tr><td><b>Сообщение:</b></td><td>{message}</td></tr>
      </table>
    </div>
    """
    tg_msg = f"⚡ <b>NUT Monitor</b>\nUPS: <b>{ups_name}</b>\nСтатус: <b>{status}</b>\n{message}"
    ntfy_msg = f"UPS: {ups_name} | Статус: {status}\n{message}"

    # Определяем приоритет ntfy по статусу
    ntfy_priority = "urgent" if "LB" in status or "FSD" in status else \
                    "high"   if "OB" in status else "default"

    send_telegram(
        settings.get("tg_token", ""),
        settings.get("tg_chat_id", ""),
        tg_msg
    )
    send_ntfy(
        settings.get("ntfy_url", ""),
        ntfy_msg,
        title=f"NUT Monitor — {ups_name}",
        priority=ntfy_priority
    )
    send_email(
        settings.get("smtp_host", ""),
        int(settings.get("smtp_port", 587)),
        settings.get("smtp_user", ""),
        settings.get("smtp_password", ""),
        settings.get("smtp_to", ""),
        subject,
        body
    )


def test_telegram(token: str, chat_id: str) -> bool:
    return send_telegram(token, chat_id, "✅ <b>NUT Monitor</b>\nTelegram уведомления работают!")


def test_ntfy(url: str) -> bool:
    return send_ntfy(url, "NUT Monitor: уведомления работают! ✅",
                     title="NUT Monitor — Тест", priority="default")


def test_email(host, port, user, password, to) -> bool:
    return send_email(host, int(port), user, password, to,
                      "[NUT Monitor] Тест уведомлений",
                      "<b>NUT Monitor</b>: Email уведомления работают!")
