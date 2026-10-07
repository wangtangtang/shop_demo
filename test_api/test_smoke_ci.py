"""CI 冒烟测试（P0 主链路）

设计目的：在 CI 的干净环境里也能跑——
- 不依赖 MySQL、不依赖手动启动 Flask 服务；
- 用 Flask 自带测试客户端发请求，用临时 SQLite 文件建库，秒级跑完；
- 覆盖最核心的"注册→登录→看商品→加购→下单"主链路，
  任何一环挂掉，CI 立即红灯，阻止问题代码合入。

这是接手已有项目时最稳的第一步：先把 P0 冒烟自动化并接进流水线，
再逐步扩模块，而不是一上来重写框架。

注意：这里不调用 create_app()，而是手动构建一个只含 API 的精简 app，
避免 create_app 内部已 db.init_app 一次、再重绑临时库时
被新版 flask_sqlalchemy 判为"同一 app 重复注册"而报错。
"""
import os
import tempfile
import pytest
from flask import Flask

from app import db


@pytest.fixture()
def client():
    """每个用例一个全新的 app + 独立临时 SQLite 文件库，互不污染、跑完即弃。
    不用 :memory:——内存库在不同连接间不共享，而 SQLAlchemy 会开多个连接。"""
    fd, db_file = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # 手动构建精简 app：db 只在这一个 app 上注册一次
        app = Flask(__name__)
        app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_file}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="smoke-test-secret",
        )
        db.init_app(app)

        # 只注册 API 蓝图（冒烟不涉及服务端渲染页面）
        from app.routes import api_bp
        app.register_blueprint(api_bp, url_prefix="/api")

        with app.app_context():
            db.create_all()
            # 冒烟只造一个商品，不依赖 seed 账号
            from app.models import Product
            db.session.add(Product(name="冒烟测试键盘", price=199.0, stock=100))
            db.session.commit()
            yield app.test_client()
            # 先结束会话、再释放连接池，确保下面能删掉临时文件
            # （Windows 上文件仍被占用时删除会报 PermissionError）
            db.session.remove()
            db.engine.dispose()
    finally:
        if os.path.exists(db_file):
            os.remove(db_file)


def _signup_login(client):
    """注册并登录，返回带 token 的请求头。"""
    client.post("/api/register",
                json={"username": "smoke_user", "password": "123456"})
    r = client.post("/api/login",
                    json={"username": "smoke_user", "password": "123456"})
    assert r.status_code == 200, r.get_data(as_text=True)
    token = r.get_json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


def _create_address(client, headers):
    """新增一个收货地址，返回地址 id（下单必选）。"""
    r = client.post("/api/addresses", headers=headers, json={
        "receiver_name": "冒烟收货人",
        "receiver_phone": "13800138000",
        "region": "北京市海淀区",
        "detail": "测试路1号",
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["data"]["id"]


def test_health_products_list(client):
    """商品列表可查、结构正确（最基础的可用性）"""
    r = client.get("/api/products")
    assert r.status_code == 200
    body = r.get_json()
    assert body["code"] == 0
    assert len(body["data"]["items"]) >= 1


def test_register_login_main_flow(client):
    """注册 + 登录主链路：能拿到 token"""
    headers = _signup_login(client)
    assert headers["Authorization"].startswith("Bearer ")


def test_login_wrong_password_rejected(client):
    """错误密码必须 401（安全底线，反向断言）"""
    client.post("/api/register",
                json={"username": "pw_user", "password": "123456"})
    r = client.post("/api/login",
                    json={"username": "pw_user", "password": "wrong"})
    assert r.status_code == 401


def test_cart_add_requires_login(client):
    """未登录加购必须被拦截（鉴权生效）"""
    r = client.post("/api/cart", json={"product_id": 1, "quantity": 1})
    assert r.status_code in (401, 403)


def test_add_cart_and_create_order(client):
    """加购 → 下单主链路：返回订单号，购物车被清空"""
    headers = _signup_login(client)
    address_id = _create_address(client, headers)

    add = client.post("/api/cart", headers=headers,
                      json={"product_id": 1, "quantity": 2})
    assert add.status_code == 200, add.get_data(as_text=True)

    order = client.post("/api/orders", headers=headers,
                        json={"address_id": address_id})
    assert order.status_code == 200, order.get_data(as_text=True)
    data = order.get_json()["data"]
    assert data["order_id"] > 0
    assert data["total"] == pytest.approx(398.0)  # 199 * 2

    # 该版本下单后清空购物车
    cart = client.get("/api/cart", headers=headers).get_json()["data"]
    assert len(cart["items"]) == 0
