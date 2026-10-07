"""CI 冒烟测试（P0 主链路）

设计目的：在 CI 的干净环境里也能跑——
- 不依赖 MySQL、不依赖手动启动 Flask 服务；
- 用 Flask 自带测试客户端发请求，用内存 SQLite 建库，秒级跑完；
- 覆盖最核心的"注册→登录→看商品→加购→下单"主链路，
  任何一环挂掉，CI 立即红灯，阻止问题代码合入。

这是接手已有项目时最稳的第一步：先把 P0 冒烟自动化并接进流水线，
再逐步扩模块，而不是一上来重写框架。
"""
import os
import tempfile
import pytest

from app import create_app, db


@pytest.fixture()
def client():
    """每个用例一个全新的 app + 独立临时 SQLite 文件库，互不污染、跑完即弃。
    不用 :memory:——内存库在不同连接间不共享，而 SQLAlchemy 会开多个连接。"""
    fd, db_file = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        app = create_app()
        app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_file}",
        )
        # 重新绑定引擎到临时库（create_app 时已按默认配置建过一次引擎）
        db.init_app(app)
        with app.app_context():
            db.drop_all()
            db.create_all()
            # 冒烟只造一个商品，不依赖 seed 账号
            from app.models import Product
            db.session.add(Product(name="冒烟测试键盘", price=199.0, stock=100))
            db.session.commit()
            yield app.test_client()
            db.session.remove()
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

    add = client.post("/api/cart", headers=headers,
                      json={"product_id": 1, "quantity": 2})
    assert add.status_code == 200, add.get_data(as_text=True)

    order = client.post("/api/orders", headers=headers)
    assert order.status_code == 200, order.get_data(as_text=True)
    data = order.get_json()["data"]
    assert data["order_id"] > 0
    assert data["total"] == pytest.approx(398.0)  # 199 * 2

    # 该版本下单后清空购物车
    cart = client.get("/api/cart", headers=headers).get_json()["data"]
    assert len(cart["items"]) == 0
