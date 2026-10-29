#!/usr/bin/env bash
# Run on the VPS after unpacking the source under /opt/medisearch.
set -euo pipefail

if [[ "${EUID}" -ne 0 || "$#" -ne 1 ]]; then
    echo "Dùng trên VPS: sudo bash deploy/install.sh https://research.example.com" >&2
    exit 1
fi
cd /opt/medisearch
deployment_origin=$(/usr/bin/python3 -c 'import sys; from medical_agent.web_security import public_origin; print(public_origin(sys.argv[1]))' "$1")
/usr/bin/python3 -c 'import sqlite3; db = sqlite3.connect(":memory:"); db.execute("CREATE VIRTUAL TABLE check_fts USING fts5(content)")'
if ! command -v ollama >/dev/null; then
    echo "Cài Ollama và tải qwen3:1.7b trước; xem docs/deployment.md." >&2
    exit 1
fi
if ! id medisearch >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/medisearch --shell /usr/sbin/nologin medisearch
fi
install -d -m 0750 -o root -g medisearch /etc/medisearch
install -d -m 0700 -o medisearch -g medisearch /var/lib/medisearch
if [[ ! -e /etc/medisearch/auth.json ]]; then
    /usr/bin/python3 -m medical_agent.web --create-auth /etc/medisearch/auth.json --username admin
fi
chown root:medisearch /etc/medisearch/auth.json
chmod 0640 /etc/medisearch/auth.json
if [[ -e /etc/medisearch/web.env ]]; then
    echo "Giữ cấu hình đã có tại /etc/medisearch/web.env; kiểm tra origin trước khi restart."
else
    printf 'MEDISEARCH_PUBLIC_ORIGIN=%s\n' "$deployment_origin" > /etc/medisearch/web.env
    chmod 0600 /etc/medisearch/web.env
fi
install -m 0644 deploy/medisearch.service /etc/systemd/system/medisearch.service
systemctl daemon-reload
systemctl enable --now medisearch.service
echo "Đã bật dịch vụ app. Cấu hình Caddy/DNS theo docs/deployment.md để mở HTTPS."
