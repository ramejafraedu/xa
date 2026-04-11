#!/bin/bash
# Instala y habilita autoarranque (Linux/VPS) para scheduler + dashboard.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHED_SRC="/tmp/video-factory.service"
DASH_SRC="/tmp/video-factory-dashboard.service"
SCHED_DST="/etc/systemd/system/video-factory.service"
DASH_DST="/etc/systemd/system/video-factory-dashboard.service"

if [ ! -f "$SCHED_SRC" ] || [ ! -f "$DASH_SRC" ]; then
  echo "[!] No se encontraron los service files en /tmp."
  echo "    Ejecuta primero: ./setup_ubuntu.sh"
  exit 1
fi

echo "[+] Instalando servicios systemd..."
sudo cp "$SCHED_SRC" "$SCHED_DST"
sudo cp "$DASH_SRC" "$DASH_DST"
sudo systemctl daemon-reload

echo "[+] Habilitando autoarranque al encender..."
sudo systemctl enable video-factory
sudo systemctl enable video-factory-dashboard

echo "[+] Iniciando servicios ahora..."
sudo systemctl restart video-factory
sudo systemctl restart video-factory-dashboard

if command -v ufw >/dev/null 2>&1; then
  echo "[+] Abriendo puerto 8000 en UFW (si aplica)..."
  sudo ufw allow 8000/tcp || true
fi

echo "[+] Estado de servicios:"
sudo systemctl --no-pager --full status video-factory | sed -n '1,12p'
sudo systemctl --no-pager --full status video-factory-dashboard | sed -n '1,12p'

echo ""
echo "Listo. Al reiniciar la maquina, el dashboard quedara disponible automaticamente."
echo "Ver logs dashboard: sudo journalctl -u video-factory-dashboard -f"
