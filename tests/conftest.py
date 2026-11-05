import os
import sqlite3
import tempfile
import time

import pytest

_TMP = tempfile.mkdtemp(prefix="dp_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def login(client, username):
    r = client.post("/api/auth/login", json={"username": username, "password": f"{username}123"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="session")
def tokens(client):
    return {role: login(client, role)
            for role in ["admin", "engineer", "analyst", "viewer"]}


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


_seq = [0]


def make_sqlite_ds(client, tok, name, ddl_rows: dict, description=""):
    """创建带表的 sqlite 数据源（自动采集元数据；重名时自动加后缀）。"""
    _seq[0] += 1
    path = os.path.join(_TMP, f"{name}_{_seq[0]}.db")
    conn = sqlite3.connect(path)
    for ddl, rows in ddl_rows.items():
        conn.execute(ddl)
        for r in rows:
            conn.execute(f"INSERT INTO {ddl.split()[2]} VALUES ({','.join('?' * len(r))})", r)
    conn.commit()
    conn.close()
    payload = {"name": name, "type": "sqlite", "params": {"path": path},
               "description": description}
    r = client.post("/api/datasources", headers=H(tok), json=payload)
    if r.status_code == 400:  # 数据源重名（跨测试复用名）→ 加后缀
        payload["name"] = f"{name}_{_seq[0]}"
        r = client.post("/api/datasources", headers=H(tok), json=payload)
    assert r.status_code == 200, r.text
    ds = r.json()
    # 立即采集元数据（对话意图匹配/资产搜索依赖元数据）
    rc = client.post(f"/api/datasources/{ds['id']}/collect", headers=H(tok))
    assert rc.status_code == 200, rc.text
    return ds


def wait_instance(client, tok, instance_id, timeout=25, expect=("success", "failed")):
    for _ in range(timeout):
        r = client.get(f"/api/sched/instances/{instance_id}", headers=H(tok))
        data = r.json()
        if data["status"] in expect:
            return data
        time.sleep(1)
    return data


def ts_token_sign(secret, app_key, params):
    import hashlib
    import hmac as hmac_mod
    ts = str(int(time.time()))
    payload = {"app_key": app_key, "timestamp": ts, "params": params or {}}
    msg = __import__("json").dumps(payload, sort_keys=True, separators=(",", ":"))
    sign = hmac_mod.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return ts, sign
