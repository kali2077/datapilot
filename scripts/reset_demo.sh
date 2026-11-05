#!/bin/bash
# 重置 DataPilot 演示环境（清空平台库与演示数仓，重启后自动重新种子）
set -e
cd "$(dirname "$0")/.."
pkill -f "uvicorn app.main" 2>/dev/null || true
sleep 1
rm -f deploy/data/datapilot.db* deploy/data/demo_warehouse.db
echo "[reset] 平台库与演示数仓已清空"
echo "[reset] 重新启动: ./.venv/bin/uvicorn app.main:app --port 8100"
