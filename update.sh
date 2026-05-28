#!/bin/bash
set -e

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${GREEN}[UPDATE]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

[ "$EUID" -ne 0 ] && error "Запускать от root"

INSTALL_DIR="/opt/nut-monitor"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

info "Репозиторий: $REPO_DIR"
info "Установка: $INSTALL_DIR"

# Обновить из git
info "Получение обновлений..."
cd "$REPO_DIR"
git pull origin master

# Копировать файлы (БД и .env не трогаем)
info "Обновление файлов приложения..."
cp backend/app.py     "$INSTALL_DIR/"
cp backend/db.py      "$INSTALL_DIR/"
cp backend/nut.py     "$INSTALL_DIR/"
cp backend/notify.py  "$INSTALL_DIR/"
cp backend/auth.py    "$INSTALL_DIR/"
cp frontend/static/index.html "$INSTALL_DIR/static/"
cp frontend/static/login.html "$INSTALL_DIR/static/"

# Обновить зависимости если изменились
info "Проверка зависимостей..."
"$INSTALL_DIR/venv/bin/pip" install -q -r "$REPO_DIR/requirements.txt"

# Перезапустить
info "Перезапуск сервиса..."
systemctl restart nut-monitor-web
sleep 2

STATUS=$(systemctl is-active nut-monitor-web)
if [ "$STATUS" = "active" ]; then
  echo -e "${GREEN}✓ Обновление завершено, сервис запущен${NC}"
else
  echo -e "${RED}✗ Сервис не запустился, проверь: journalctl -u nut-monitor-web -n 20${NC}"
fi
