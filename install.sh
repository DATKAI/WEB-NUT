#!/bin/bash
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[NUT-Monitor]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

[ "$EUID" -ne 0 ] && error "Запускать от root"

INSTALL_DIR="/opt/nut-monitor"
DATA_DIR="/opt/nut-monitor"

info "=== NUT Monitor — установка ==="

# 1. Репозитории Proxmox
if [ -f /etc/apt/sources.list.d/pve-enterprise.list ]; then
  info "Переключение на бесплатные репозитории Proxmox..."
  echo "# deb https://enterprise.proxmox.com/debian/pve bookworm pve-enterprise" \
    > /etc/apt/sources.list.d/pve-enterprise.list
  echo "# deb https://enterprise.proxmox.com/debian/ceph-quincy bookworm enterprise" \
    > /etc/apt/sources.list.d/ceph.list 2>/dev/null || true
  echo "deb http://download.proxmox.com/debian/pve bookworm pve-no-subscription" \
    > /etc/apt/sources.list.d/pve-no-subscription.list
fi

# 2. Системные зависимости
info "Установка пакетов..."
apt-get update -q
apt-get install -y -q nut nut-client nut-server python3-venv python3-pip

# 3. Директория
info "Создание директории $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR/static"

# 4. Python venv
info "Создание Python окружения..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q fastapi uvicorn websockets

# 5. Копирование файлов
info "Копирование файлов приложения..."
cp backend/app.py     "$INSTALL_DIR/"
cp backend/db.py      "$INSTALL_DIR/"
cp backend/nut.py     "$INSTALL_DIR/"
cp backend/notify.py  "$INSTALL_DIR/"
cp backend/auth.py    "$INSTALL_DIR/"
cp frontend/static/index.html "$INSTALL_DIR/static/"
cp frontend/static/login.html "$INSTALL_DIR/static/"

# 6. Пароли
info "Генерация паролей..."
ADMIN_PASS=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16)
MONITOR_PASS=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16)
SLAVE_PASS=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16)

# 7. NUT конфиг
info "Настройка NUT..."
echo "MODE=netserver" > /etc/nut/nut.conf

cat > /etc/nut/upsd.conf << EOF
LISTEN 0.0.0.0 3493
MAXAGE 15
EOF

cat > /etc/nut/upsd.users << EOF
[admin]
    password = $ADMIN_PASS
    actions = SET
    instcmds = ALL

[monitor]
    password = $MONITOR_PASS
    upsmon master

[upslave]
    password = $SLAVE_PASS
    upsmon slave
EOF
chmod 640 /etc/nut/upsd.users

# ups.conf — пустой, заполняется через панель
[ -f /etc/nut/ups.conf ] || echo "# Управляется через NUT Monitor" > /etc/nut/ups.conf

# upsmon.conf — базовый
cat > /etc/nut/upsmon.conf << EOF
MINSUPPLIES 0
SHUTDOWNCMD "/sbin/shutdown -h +0"
POLLFREQ 5
POLLFREQALERT 5
HOSTSYNC 15
DEADTIME 15
POWERDOWNFLAG /etc/killpower
NOTIFYFLAG ONLINE   SYSLOG+WALL
NOTIFYFLAG ONBATT   SYSLOG+WALL
NOTIFYFLAG LOWBATT  SYSLOG+WALL
NOTIFYFLAG SHUTDOWN SYSLOG+WALL
EOF

# 8. Сохранить NUT пароли в БД при первом старте — через env
cat > "$INSTALL_DIR/.env" << EOF
NUT_ADMIN_PASS=$ADMIN_PASS
NUT_MONITOR_PASS=$MONITOR_PASS
NUT_SLAVE_PASS=$SLAVE_PASS
EOF
chmod 600 "$INSTALL_DIR/.env"

# 9. systemd сервис NUT Monitor
info "Создание systemd сервиса..."
cat > /etc/systemd/system/nut-monitor-web.service << EOF
[Unit]
Description=NUT Monitor Web Panel
After=network.target nut-server.service

[Service]
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python app.py
Restart=always
RestartSec=5
User=root
EnvironmentFile=$INSTALL_DIR/.env

[Install]
WantedBy=multi-user.target
EOF

# 10. Запуск
info "Запуск сервисов..."
systemctl daemon-reload
systemctl enable --now nut-server nut-monitor nut-monitor-web 2>/dev/null || true
systemctl restart nut-monitor-web

# 11. IP сервера
SERVER_IP=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         NUT Monitor — установка завершена        ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} Панель:   ${YELLOW}http://$SERVER_IP:8000${NC}"
echo -e "${GREEN}║${NC} Логин:    ${YELLOW}admin${NC}"
echo -e "${GREEN}║${NC} Пароль:   ${YELLOW}admin${NC}  (сменить после входа!)"
echo -e "${GREEN}╠══════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} NUT пароли (сохранены в $INSTALL_DIR/.env):"
echo -e "${GREEN}║${NC} admin:    $ADMIN_PASS"
echo -e "${GREEN}║${NC} monitor:  $MONITOR_PASS"
echo -e "${GREEN}║${NC} upslave:  $SLAVE_PASS"
echo -e "${GREEN}╠══════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} Клиентам Linux прописать в upsmon.conf:"
echo -e "${GREEN}║${NC} MONITOR <ups>@$SERVER_IP 1 upslave $SLAVE_PASS slave"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
info "Добавьте ИБП через веб-панель: Устройства → Сканировать USB"
