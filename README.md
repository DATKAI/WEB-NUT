<div align="center">

<img src="https://raw.githubusercontent.com/DATKAI/WEB-NUT/master/docs/logo.svg" alt="NUT Monitor" width="80" height="80">

# ⚡ NUT Monitor

**Веб-панель управления и мониторинга ИБП на базе Network UPS Tools**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![SQLite](https://img.shields.io/badge/SQLite-3-003B57?style=flat-square&logo=sqlite&logoColor=white)](https://sqlite.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Proxmox](https://img.shields.io/badge/Proxmox-Debian-E57000?style=flat-square&logo=proxmox&logoColor=white)](https://proxmox.com)

*Мониторинг в реальном времени · Уведомления без VPN · Клиенты Windows и Linux*

</div>

---

## 📸 Скриншоты

| Дашборд (тёмная тема) | Дашборд (светлая тема) |
|:---:|:---:|
| ![Dark](docs/dashboard-dark.png) | ![Light](docs/dashboard-light.png) |

---

## ✨ Возможности

<table>
<tr>
<td width="50%">

**📊 Мониторинг**
- Дашборд в реальном времени (WebSocket, каждые 3 сек)
- Несколько ИБП одновременно
- Заряд, нагрузка, напряжение, частота, температура АКБ
- Графики за 1 / 6 / 24 часа (Chart.js)
- Запас хода от батареи

</td>
<td width="50%">

**🖥️ Клиенты**
- Таблица подключённых серверов в реальном времени
- Windows: служба SYSTEM через WinSW (без входа пользователя)
- Linux: systemd timer heartbeat
- Статус, заряд, время последней активности

</td>
</tr>
<tr>
<td width="50%">

**🔔 Уведомления**
- **ntfy.sh** — Push без VPN (работает в России)
- **Telegram** Bot API
- **Email** / SMTP
- Переход на батарею, низкий заряд, восстановление питания, отключение связи

</td>
<td width="50%">

**📋 История и статистика**
- Журнал событий с поиском, фильтром и пагинацией
- Экспорт в CSV
- История отключений: когда, длительность, минимальный заряд
- Статистика: среднее время, количество за 30 дней

</td>
</tr>
<tr>
<td width="50%">

**⚙️ Управление NUT**
- Конфигурация без редактирования файлов
- Добавление/удаление ИБП через USB-сканирование
- Управление пользователями NUT (upsd.users)
- Перезапуск NUT без потери настроек

</td>
<td width="50%">

**🎨 Интерфейс**
- Тёмная и светлая тема (переключатель, сохраняется)
- Статус-бар в шапке: ИБП + клиенты + NUT
- Русский язык
- Без JS-фреймворков (Vanilla JS)

</td>
</tr>
</table>

---

## 🚀 Быстрая установка

> **Требования:** Proxmox / Debian 11+ / Ubuntu 22+, Python 3.11+, NUT установлен и настроен

```bash
apt install -y git
git clone https://github.com/DATKAI/WEB-NUT.git
cd WEB-NUT
bash install.sh
```

Скрипт автоматически:
- Установит зависимости Python
- Создаст базу данных SQLite
- Сгенерирует случайные пароли
- Настроит systemd службу
- Запустит панель

После установки в терминале появится адрес панели и учётные данные.

---

## 🔄 Обновление

```bash
cd /root/WEB-NUT && bash update.sh
```

База данных и настройки сохраняются при обновлении.

---

## 🖥️ Подключение клиентов

### Linux (Debian / Ubuntu)

```bash
apt install -y nut-client
echo "MODE=netclient" > /etc/nut/nut.conf

cat > /etc/nut/upsmon.conf << 'EOF'
MONITOR ippon@192.168.10.3 1 upslave YOUR_PASSWORD slave
MINSUPPLIES 1
SHUTDOWNCMD "/sbin/shutdown -h +0"
POLLFREQ 5
POLLFREQALERT 5
DEADTIME 15
EOF

systemctl enable --now nut-client
```

Для отображения в панели — установить heartbeat скрипт (шаг 6 в разделе «Клиенты»).

---

### Windows (служба без входа пользователя)

1. В панели → **Клиенты → Windows** → скачать `nut-service-install.zip`
2. Распаковать все файлы в одну папку
3. Запустить `nut-service-install.bat` **от администратора**

Скрипт скачает WinSW, создаст службу `NUT-Monitor` от имени SYSTEM.  
Служба запускается автоматически при старте Windows — без входа пользователя.

**Логи:** `C:\NUT-Monitor\nut-monitor.log`

---

## 🔔 Уведомления без VPN (ntfy)

1. Установи приложение [ntfy](https://ntfy.sh) на телефон
2. Подпишись на свою тему, например: `nut-monitor-myserver`
3. В панели → **Уведомления → ntfy** → укажи URL:

```
https://ntfy.sh/nut-monitor-myserver
```

> ntfy.sh работает в России без VPN. При отключении питания придёт мгновенное push-уведомление с зарядом и временем работы от батареи.

---

## 🏗️ Архитектура

```
┌─────────────────────────────────────────────────────────┐
│                    Proxmox / Debian                      │
│                                                          │
│  ┌──────────┐    ┌────────────┐    ┌─────────────────┐  │
│  │ ИБП #1   │    │ ИБП #2     │    │   NUT Monitor   │  │
│  │ (Ippon)  │    │ (Powercom) │    │   FastAPI :8000 │  │
│  └────┬─────┘    └─────┬──────┘    └────────┬────────┘  │
│       │  USB           │  USB               │           │
│       └────────────────┘                    │           │
│              upsd :3493 ◄───────────────────┘           │
└─────────────────────────────────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              │                         │
    ┌─────────▼──────────┐   ┌─────────▼──────────┐
    │  Linux клиенты     │   │  Windows клиенты   │
    │  upsmon + heartbeat│   │  NUT Monitor Svc   │
    └────────────────────┘   └────────────────────┘
```

---

## 🛠️ Стек технологий

| Компонент | Технология |
|-----------|-----------|
| Backend | Python 3.11 + FastAPI + uvicorn |
| База данных | SQLite (настройки, события, метрики) |
| Frontend | Vanilla JS + HTML/CSS (без фреймворков) |
| Графики | Chart.js 4 |
| Real-time | WebSocket |
| NUT | usbhid-ups, upsd, upsmon |
| Уведомления | ntfy / Telegram Bot API / SMTP |
| Windows служба | WinSW |
| Linux таймер | systemd timer |

---

## 📁 Структура проекта

```
WEB-NUT/
├── backend/
│   ├── app.py          # FastAPI приложение, WebSocket, API
│   ├── db.py           # SQLite: схема, CRUD операции
│   ├── nut.py          # Обёртка над upsc / upscmd / upsdrvctl
│   ├── notify.py       # ntfy / Telegram / Email уведомления
│   └── auth.py         # Сессионная аутентификация
├── frontend/
│   └── static/
│       ├── index.html  # SPA панель (Vanilla JS)
│       ├── login.html  # Страница входа
│       └── guide.html  # Руководство пользователя
├── install.sh          # Установка одной командой
└── update.sh           # Обновление с сохранением данных
```

---

## 📖 Документация

Встроенное руководство доступно в панели по кнопке **📖 Руководство** или по адресу:

```
http://<IP_СЕРВЕРА>:8000/static/guide.html
```

---

## 🧪 Тестировалось на

- Proxmox VE 8.x (Debian Bookworm)
- ИБП: **Ippon Innova G2 Euro 2000** (vendorid `06da:ffff`, драйвер `usbhid-ups`)
- ИБП: **Powercom SPT-2000-II** (vendorid `0d9f:0004`, драйвер `usbhid-ups`)
- Клиенты: Debian 12, Ubuntu 22.04, Windows 10/11 Server

---

## 📄 Лицензия

[MIT](LICENSE) © 2026 DATKAI

---

<div align="center">

Если проект оказался полезным — поставь ⭐

</div>
