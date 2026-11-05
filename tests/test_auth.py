"""认证与权限测试。"""


def test_login_wrong_password(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_me(client, tokens):
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_no_token(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_viewer_cannot_list_users(client, tokens):
    r = client.get("/api/auth/users", headers={"Authorization": f"Bearer {tokens['viewer']}"})
    assert r.status_code == 403


def test_admin_create_and_update_user(client, tokens):
    r = client.post("/api/auth/users", headers={"Authorization": f"Bearer {tokens['admin']}"},
                    json={"username": "tempuser", "password": "temp123456", "role": "analyst",
                          "display_name": "临时用户"})
    assert r.status_code == 200
    uid = r.json()["id"]
    r = client.put(f"/api/auth/users/{uid}",
                   headers={"Authorization": f"Bearer {tokens['admin']}"},
                   json={"status": "disabled"})
    assert r.status_code == 200
    # 被停用用户登录被拒
    r = client.post("/api/auth/login", json={"username": "tempuser", "password": "temp123456"})
    assert r.status_code == 403


def test_analyst_cannot_create_datasource(client, tokens):
    r = client.post("/api/datasources", headers={"Authorization": f"Bearer {tokens['analyst']}"},
                    json={"name": "x", "type": "sqlite", "params": {"path": "/tmp/x.db"}})
    assert r.status_code == 403
