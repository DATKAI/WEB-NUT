# WEB-NUT Monitor

Веб-панель для управления и мониторинга ИБП через NUT (Network UPS Tools).

## Возможности

- 📊 Мониторинг ИБП в реальном времени (заряд, нагрузка, напряжение, температура)
- 🔌 Управление устройствами через GUI (добавление, удаление, сканирование USB)
- ⚙️ Управление NUT сервером (перезапуск, конфигурация, пользователи)
- 🔔 Уведомления: Telegram + Email + Push в браузере
- 👥 Пользователи панели с ролями (admin / viewer)
- 📋 Журнал событий и история метрик
- 🔒 Авторизация через сессии

## Быстрая установка (Proxmox / Debian)

```bash
apt install -y git
git clone https://github.com/DATKAI/WEB-NUT.git
cd WEB-NUT
bash install.sh
```

После установки откройте ссылку из вывода скрипта.

## Подключение клиентов Linux

```bash
apt install -y nut-client
echo "MODE=netclient" > /etc/nut/nut.conf
cat > /etc/nut/upsmon.conf << EOF
MONITOR ippon@<IP_СЕРВЕРА> 1 upslave <SLAVE_PASS> slave
MINSUPPLIES 1
SHUTDOWNCMD "/sbin/shutdown -h +0"
EOF
systemctl enable --now nut-client
```

## Подключение клиентов Windows

Использовать [WinNUT-Client](https://github.com/nutdotnet/WEB-NUT-Client/releases):
- Host: IP сервера
- Port: 3493
- UPS: ippon (или powercom)
- Login: upslave
- Password: из вывода install.sh

## Стек

- **Backend**: Python 3.11 + FastAPI + SQLite
- **Frontend**: Vanilla JS + HTML/CSS (без фреймворков)
- **NUT**: usbhid-ups, blazer_ser и другие драйверы
- **Уведомления**: Telegram Bot API, SMTP
