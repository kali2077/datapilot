"""数据服务 e2e 测试（M3）：AK/SK 签名、授权、限流、缓存、审计。"""
from conftest import H, make_sqlite_ds, ts_token_sign

ORDERS_DDL = "CREATE TABLE orders (id INTEGER PRIMARY KEY, city TEXT, amount REAL)"


def _setup_api(client, tokens, code="daily_amt", rate=10):
    ds = make_sqlite_ds(
        client, tokens["engineer"], f"a_src_{code}",
        {ORDERS_DDL: [(i, ["北京", "上海"][i % 2], i * 3.0) for i in range(1, 21)]})
    h = H(tokens["engineer"])
    r = client.post("/api/apigw/apis", headers=h, json={
        "code": code, "name": "每日金额", "datasource_id": ds["id"],
        "sql_template": "SELECT city, SUM(amount) AS total FROM orders WHERE 1=1 GROUP BY city",
        "params": [], "cache_ttl": 0, "row_limit": 100, "timeout_ms": 5000})
    assert r.status_code == 200, r.text
    api_id = r.json()["id"]
    r = client.post(f"/api/apigw/apis/{api_id}/publish", headers=h)
    assert r.status_code == 200
    return api_id


def test_api_publish_and_call(client, tokens):
    api_id = _setup_api(client, tokens, "api_pub")
    h = H(tokens["admin"])
    # 创建应用（secret 仅一次）
    r = client.post("/api/apigw/apps", headers=h,
                    json={"app_name": "测试应用", "rate_limit": 10})
    assert r.status_code == 200
    app_info = r.json()
    assert app_info["app_secret"].startswith("sk_")
    app_id = app_info["id"]
    # 授权
    r = client.post(f"/api/apigw/apps/{app_id}/grants", headers=h,
                    json={"api_id": api_id})
    assert r.status_code == 200
    # 签名调用
    ts, sign = ts_token_sign(app_info["app_secret"], app_info["app_key"], {})
    r = client.post("/api/v1/query/api_pub",
                    headers={"X-App-Key": app_info["app_key"],
                             "X-Timestamp": ts, "X-Sign": sign},
                    json={"params": {}})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert {row[0] for row in data["rows"]} == {"北京", "上海"}
    assert data["row_count"] == 2
    # 审计日志
    r = client.get("/api/apigw/logs?api_id=" + str(api_id), headers=H(tokens["engineer"]))
    assert r.json()["total"] >= 1


def test_api_signature_and_grant_enforced(client, tokens):
    api_id = _setup_api(client, tokens, "api_sec")
    h = H(tokens["admin"])
    r = client.post("/api/apigw/apps", headers=h,
                    json={"app_name": "无授权应用", "rate_limit": 10})
    app_info = r.json()
    # 未授权 -> 403
    ts, sign = ts_token_sign(app_info["app_secret"], app_info["app_key"], {})
    r = client.post("/api/v1/query/api_sec",
                    headers={"X-App-Key": app_info["app_key"], "X-Timestamp": ts, "X-Sign": sign},
                    json={"params": {}})
    assert r.status_code == 403
    # 错签名 -> 401
    r = client.post(f"/api/apigw/apps/{app_info['id']}/grants", headers=h,
                    json={"api_id": api_id})
    ts, _ = ts_token_sign(app_info["app_secret"], app_info["app_key"], {})
    r = client.post("/api/v1/query/api_sec",
                    headers={"X-App-Key": app_info["app_key"], "X-Timestamp": ts,
                             "X-Sign": "bad"},
                    json={"params": {}})
    assert r.status_code == 401


def test_api_rate_limit(client, tokens):
    api_id = _setup_api(client, tokens, "api_rate")
    h = H(tokens["admin"])
    r = client.post("/api/apigw/apps", headers=h,
                    json={"app_name": "限流应用", "rate_limit": 1})
    app_info = r.json()
    client.post(f"/api/apigw/apps/{app_info['id']}/grants", headers=h,
                json={"api_id": api_id})
    for i in range(2):
        ts, sign = ts_token_sign(app_info["app_secret"], app_info["app_key"], {})
        r = client.post("/api/v1/query/api_rate",
                        headers={"X-App-Key": app_info["app_key"],
                                 "X-Timestamp": ts, "X-Sign": sign},
                        json={"params": {}})
        if i == 0:
            assert r.status_code == 200
    assert r.status_code == 403  # 第二次同秒调用被限流


def test_api_params_and_validation(client, tokens):
    ds = make_sqlite_ds(client, tokens["engineer"], "a_src_p",
                        {ORDERS_DDL: [(i, "北京", i * 1.0) for i in range(1, 6)]})
    h = H(tokens["engineer"])
    r = client.post("/api/apigw/apis", headers=h, json={
        "code": "api_param", "name": "带参数", "datasource_id": ds["id"],
        "sql_template": "SELECT * FROM orders WHERE city = :city",
        "params": [{"name": "city", "type": "string", "required": True,
                    "default": "", "validation": ""}]})
    api_id = r.json()["id"]
    # 试执行
    r = client.post(f"/api/apigw/apis/{api_id}/test-execute", headers=h,
                    json={"params": {"city": "北京"}})
    assert r.status_code == 200
    assert r.json()["row_count"] == 5
    # 非只读 SQL 被拒
    r = client.post("/api/apigw/apis", headers=h, json={
        "code": "api_bad", "name": "坏SQL", "datasource_id": ds["id"],
        "sql_template": "DELETE FROM orders"})
    assert r.status_code == 400
