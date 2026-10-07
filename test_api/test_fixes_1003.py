"""BUG-004 / 005 / 006 修复后的回归用例（2026-10-03）

- BUG-004：购物车 quantity 非整数（字母/小数/None/布尔）必须返回 400，不再 500
- BUG-005：下单超过 30 分钟未支付 → 惰性自动取消、回补库存，支付/回调被拒
- BUG-006：/admin 页面服务端鉴权——未登录跳转、非管理员 403、管理员 200
"""
import time
import pytest
import requests


# ---------------- BUG-004：购物车数量非法输入 ----------------

@pytest.mark.parametrize("bad_qty", ["abc", 1.5, None, True])
def test_add_cart_non_integer_quantity_returns_400(
        auth_headers, requests_session, base_url, bad_qty):
    """POST /api/cart：quantity 传字母/小数/None/布尔，必须 400 且不是 500"""
    r = requests_session.post(
        f"{base_url}/cart", headers=auth_headers,
        json={"product_id": 1, "quantity": bad_qty})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == 400


def test_update_cart_non_integer_quantity_returns_400(
        auth_headers, requests_session, base_url):
    """PUT /api/cart/<id>：改数量传非整数同样 400（先正常加一条）"""
    requests_session.post(f"{base_url}/cart", headers=auth_headers,
                          json={"product_id": 1, "quantity": 1})
    items = requests_session.get(f"{base_url}/cart",
                                 headers=auth_headers).json()["data"]["items"]
    item_id = items[0]["id"]

    r = requests_session.put(f"{base_url}/cart/{item_id}", headers=auth_headers,
                             json={"quantity": "not_a_number"})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == 400


# ---------------- BUG-005：支付超时关单 ----------------

def _backdate_order_created_at(order_id, minutes=31):
    """直连库把订单 created_at 往回拨 minutes 分钟，模拟"下单很久未支付"。"""
    from datetime import datetime, timedelta
    from app import create_app, db
    from app.models import Order
    app = create_app()
    with app.app_context():
        order = db.session.get(Order, order_id)
        assert order is not None, "订单不存在，无法回拨时间"
        order.created_at = datetime.utcnow() - timedelta(minutes=minutes)
        db.session.commit()


def _signup_login(s, root_api, tag):
    """在独立会话 s 中注册并登录新用户，返回 (headers, user_id)"""
    uname = f"fix_{tag}_{int(time.time())}"
    s.post(f"{root_api}/register", json={"username": uname, "password": "123456"})
    r = s.post(f"{root_api}/login", json={"username": uname, "password": "123456"})
    d = r.json()["data"]
    return {"Authorization": f"Bearer {d['token']}"}, d["user_id"]


def test_expired_order_auto_cancelled_on_detail(requests_session, base_url):
    """查看详情：超时的待支付单被自动取消，状态为 cancelled"""
    s = requests.Session()
    try:
        headers, _ = _signup_login(s, base_url, "exp")
        # conftest helper：加购+地址+下单
        from conftest import place_order_with_address
        oid, _ = place_order_with_address(s, base_url, headers)

        _backdate_order_created_at(oid, minutes=31)

        r = s.get(f"{base_url}/orders/{oid}", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "cancelled"
    finally:
        s.close()


def test_pay_rejected_for_expired_order(requests_session, base_url):
    """对超时订单发起支付：返回 400，订单保持 cancelled"""
    s = requests.Session()
    try:
        headers, _ = _signup_login(s, base_url, "pay")
        from conftest import place_order_with_address
        oid, _ = place_order_with_address(s, base_url, headers)
        _backdate_order_created_at(oid, minutes=31)

        r = s.post(f"{base_url}/orders/{oid}/pay",
                   headers=headers, json={"channel": "wechat"})
        assert r.status_code == 400, r.text

        detail = s.get(f"{base_url}/orders/{oid}", headers=headers).json()["data"]
        assert detail["status"] == "cancelled"
    finally:
        s.close()


def test_late_notify_rejected_for_expired_order(requests_session, base_url):
    """超时订单的迟到支付回调：被拒绝（notify_result=fail），不会被置成已付"""
    s = requests.Session()
    try:
        headers, _ = _signup_login(s, base_url, "ntf")
        from conftest import place_order_with_address
        oid, _ = place_order_with_address(s, base_url, headers)
        _backdate_order_created_at(oid, minutes=31)

        r = s.post(f"{base_url}/pay/mock-notify", json={"order_id": oid})
        assert r.status_code == 400, r.text
        assert r.json().get("notify_result") == "fail"

        detail = s.get(f"{base_url}/orders/{oid}", headers=headers).json()["data"]
        assert detail["status"] == "cancelled"
    finally:
        s.close()


def test_recent_order_not_expired(requests_session, base_url):
    """正常时效内的订单不会被误取消（边界保护）"""
    s = requests.Session()
    try:
        headers, _ = _signup_login(s, base_url, "ok")
        from conftest import place_order_with_address
        oid, _ = place_order_with_address(s, base_url, headers)
        # 不回拨时间，立即查看
        r = s.get(f"{base_url}/orders/{oid}", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "pending"
    finally:
        s.close()


# ---------------- BUG-006：/admin 页面服务端鉴权 ----------------

ROOT = "http://127.0.0.1:5000"


def test_admin_page_redirects_when_not_logged_in():
    """未登录直接打开 /admin 页面：302 跳转到登录页"""
    s = requests.Session()
    try:
        r = s.get(f"{ROOT}/admin", allow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers.get("Location", "")
    finally:
        s.close()


def test_admin_page_403_for_normal_user():
    """普通顾客登录后打开 /admin：403"""
    s = requests.Session()
    try:
        _signup_login(s, f"{ROOT}/api", "usr")
        r = s.get(f"{ROOT}/admin", allow_redirects=False)
        assert r.status_code == 403
    finally:
        s.close()


def test_admin_page_200_for_admin():
    """管理员登录后打开 /admin：200"""
    s = requests.Session()
    try:
        r = s.post(f"{ROOT}/api/login",
                   json={"username": "admin", "password": "admin123"})
        assert r.status_code == 200, r.text
        page = s.get(f"{ROOT}/admin", allow_redirects=False)
        assert page.status_code == 200
    finally:
        s.close()
