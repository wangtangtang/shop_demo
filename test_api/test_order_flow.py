"""订单主流程测试（原作者 A 在 pull 同事更新后新增）

覆盖订单从下单到完成的核心状态机：
  pending(待支付) --pay--> paid(待发货) --管理员发货--> shipped(已发货)
       --confirm--> completed(已完成)
  以及待支付取消、未带地址下单、非法状态操作等负向场景。

全程用 Flask 测试客户端 + 临时 SQLite，不依赖外部服务；
管理员也走 Bearer token（直接在库里造 is_admin 用户再登录）。
"""
import os
import tempfile
import pytest
from flask import Flask

from app import db


@pytest.fixture()
def client():
    fd, db_file = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        app = Flask(__name__)
        app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_file}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="order-flow-secret",
        )
        db.init_app(app)
        from app.routes import api_bp
        app.register_blueprint(api_bp, url_prefix="/api")
        with app.app_context():
            db.create_all()
            from app.models import Product
            p = Product(name="订单测试鼠标", price=88.0, stock=50)
            db.session.add(p)
            db.session.commit()
            yield app.test_client()
            db.session.remove()
            db.engine.dispose()
    finally:
        if os.path.exists(db_file):
            os.remove(db_file)


def _login(client, username, password):
    r = client.post("/api/login",
                    json={"username": username, "password": password})
    assert r.status_code == 200, r.get_data(as_text=True)
    return {"Authorization": f"Bearer {r.get_json()['data']['token']}"}


def _make_buyer(client, username="buyer_a"):
    """注册普通买家并登录，返回请求头"""
    client.post("/api/register",
                json={"username": username, "password": "123456"})
    return _login(client, username, "123456")


def _make_admin(client, username="boss_a", password="admin123"):
    """直接在库里造管理员账号并登录，返回请求头"""
    from app.models import User
    admin = User(username=username, is_admin=1)
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    return _login(client, username, password)


def _make_address(client, headers):
    r = client.post("/api/addresses", headers=headers, json={
        "receiver_name": "订单收货人",
        "receiver_phone": "13800138000",
        "region": "上海市浦东新区",
        "detail": "订单路8号",
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["data"]["id"]


def _add_to_cart_and_order(client, headers, address_id, quantity=1):
    """加购 → 下单，返回下单响应的 json"""
    client.post("/api/cart", headers=headers,
                json={"product_id": 1, "quantity": quantity})
    r = client.post("/api/orders", headers=headers,
                    json={"address_id": address_id})
    return r


def test_create_order_with_address_success(client):
    """带地址下单成功：返回订单号、金额正确、状态为待支付"""
    headers = _make_buyer(client)
    addr_id = _make_address(client, headers)
    r = _add_to_cart_and_order(client, headers, addr_id, quantity=2)
    assert r.status_code == 200, r.get_data(as_text=True)
    data = r.get_json()["data"]
    assert data["order_id"] > 0
    assert data["status"] == "pending"
    assert data["total"] == pytest.approx(176.0)  # 88 * 2


def test_create_order_without_address_rejected(client):
    """不带收货地址下单：必须 400"""
    headers = _make_buyer(client)
    client.post("/api/cart", headers=headers,
                json={"product_id": 1, "quantity": 1})
    r = client.post("/api/orders", headers=headers, json={})
    assert r.status_code == 400
    assert "地址" in r.get_json()["msg"]


def test_cancel_pending_order_restores_stock(client):
    """待支付订单取消：状态变已取消，库存退回"""
    headers = _make_buyer(client)
    addr_id = _make_address(client, headers)
    order = _add_to_cart_and_order(client, headers, addr_id).get_json()["data"]
    oid = order["order_id"]

    r = client.post(f"/api/orders/{oid}/action", headers=headers,
                    json={"action": "cancel"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["data"]["status"] == "cancelled"

    # 库存应从 49 退回 50
    from app.models import Product
    assert Product.query.get(1).stock == 50


def test_pay_pending_order_becomes_paid(client):
    """待支付订单付款：状态变待发货(paid)"""
    headers = _make_buyer(client)
    addr_id = _make_address(client, headers)
    oid = _add_to_cart_and_order(client, headers, addr_id).get_json()["data"]["order_id"]

    r = client.post(f"/api/orders/{oid}/action", headers=headers,
                    json={"action": "pay", "channel": "wechat"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["data"]["status"] == "paid"


def test_admin_ship_then_buyer_confirm_completes_order(client):
    """完整履约链路：买家付款 → 管理员发货 → 买家确认收货 → 已完成"""
    buyer = _make_buyer(client)
    addr_id = _make_address(client, buyer)
    oid = _add_to_cart_and_order(client, buyer, addr_id).get_json()["data"]["order_id"]
    client.post(f"/api/orders/{oid}/action", headers=buyer,
                json={"action": "pay"})

    admin = _make_admin(client)
    ship = client.post(f"/api/admin/orders/{oid}/ship", headers=admin, json={
        "logistics_company": "顺丰速运",
        "tracking_no": "SF1234567890",
    })
    assert ship.status_code == 200, ship.get_data(as_text=True)

    confirm = client.post(f"/api/orders/{oid}/action", headers=buyer,
                          json={"action": "confirm"})
    assert confirm.status_code == 200, confirm.get_data(as_text=True)
    assert confirm.get_json()["data"]["status"] == "completed"


def test_confirm_completed_order_is_rejected(client):
    """已完成订单再次确认收货：当前状态不允许此操作 → 400"""
    buyer = _make_buyer(client)
    addr_id = _make_address(client, buyer)
    oid = _add_to_cart_and_order(client, buyer, addr_id).get_json()["data"]["order_id"]
    client.post(f"/api/orders/{oid}/action", headers=buyer, json={"action": "pay"})
    admin = _make_admin(client)
    client.post(f"/api/admin/orders/{oid}/ship", headers=admin,
                json={"logistics_company": "圆通速递", "tracking_no": "YT001"})
    client.post(f"/api/orders/{oid}/action", headers=buyer,
                json={"action": "confirm"})

    again = client.post(f"/api/orders/{oid}/action", headers=buyer,
                        json={"action": "confirm"})
    assert again.status_code == 400
