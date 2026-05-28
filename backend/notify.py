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

    send_telegram(
        settings.get("tg_token", ""),
        settings.get("tg_chat_id", ""),
        tg_msg
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


def test_email(host, port, user, password, to) -> bool:
    return send_email(host, int(port), user, password, to,
                      "[NUT Monitor] Тест уведомлений",
                      "<b>NUT Monitor</b>: Email уведомления работают!")
