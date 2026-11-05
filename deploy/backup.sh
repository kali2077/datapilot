#!/bin/bash
# DataPilot 每日备份：平台库 + 演示/工作文件。建议加入 crontab：
#   0 1 * * * /path/to/data-platform/deploy/backup.sh >> /var/log/datapilot_backup.log 2>&1
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$(dirname "$0")/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
TS=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

# 1) SQLite（默认部署）
DB_FILE="$(dirname "$0")/data/datapilot.db"
if [ -f "$DB_FILE" ]; then
  sqlite3 "$DB_FILE" ".backup '$BACKUP_DIR/datapilot_$TS.db'"
  echo "[backup] sqlite -> datapilot_$TS.db"
fi

# 2) MySQL（生产部署，DATABASE_URL 为 mysql 时启用）
if [ -n "${MYSQL_HOST:-}" ]; then
  mysqldump -h "$MYSQL_HOST" -P "${MYSQL_PORT:-3306}" -u "${MYSQL_USER:-datapilot}" \
    -p"${MYSQL_PASS:?}" "${MYSQL_DB:-datapilot}" \
    | gzip > "$BACKUP_DIR/datapilot_mysql_$TS.sql.gz"
  echo "[backup] mysql -> datapilot_mysql_$TS.sql.gz"
fi

# 3) 保留策略
find "$BACKUP_DIR" -name "datapilot*" -mtime +"$KEEP_DAYS" -delete
echo "[backup] done $TS"
