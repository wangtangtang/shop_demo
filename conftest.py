import pytest
import os
import sys

# 让测试目录能 import 到 app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def prepare_stock(base_url, requests_session):
    """开跑前自动补足 5 个初始商品的库存（只加不减，不删任何数据）

    为什么需要它：
    测试用例每次下单都会真实扣库存（已支付/待发货订单不还库存），
    手动在网站上造订单（比如测后台分页）也会扣。多次运行后
    「机械键盘」等商品可能被买空，之后再跑用例就会报 400「库存不足」。
    这个 fixture 每次 pytest 启动时自动把库存补到 200，
    这样测试不再依赖先跑 reset_db.py，库里的订单数据也不会被清掉。
    """
    try:
        resp = requests_session.post(f"{base_url}/login",
                                     json={"username": "admin", "password": "admin123"})
        token = resp.json()["data"]["token"]
        headers = {"Authorization": f"Bearer {token}"}
        for pid in range(1, 6):  # seed.py 创建的 5 个初始商品
            r = requests_session.get(f"{base_url}/products/{pid}")
            if r.status_code == 200 and r.json()["data"]["stock"] < 200:
                requests_session.put(f"{base_url}/admin/products/{pid}",
                                     json={"stock": 200}, headers=headers)
    except Exception:
        pass  # 服务没起来等情况让用例自己报错，这里不拦着


@pytest.fixture(scope="session")
def base_url():
    return "http://127.0.0.1:5000/api"


@pytest.fixture(scope="session")
def register_user(base_url, requests_session):
    """注册并登录一个测试用户，返回 (user_id, token)"""
    import time
    username = f"testuser_{int(time.time())}"
    password = "123456"

    requests_session.post(f"{base_url}/register",
                          json={"username": username, "password": password})
    resp = requests_session.post(f"{base_url}/login",
                                 json={"username": username, "password": password})
    data = resp.json()["data"]
    return data["user_id"], data["token"]


@pytest.fixture(scope="session")
def requests_session():
    import requests
    s = requests.Session()
    yield s
    s.close()


@pytest.fixture
def auth_headers(register_user):
    _, token = register_user
    return {"Authorization": f"Bearer {token}"}


# ---------- 收货地址簿相关公共工具（地址簿 + 物流功能新增） ----------

def create_address(requests_session, base_url, headers, **overrides):
    """给 headers 对应用户新增一条收货地址，返回 address_id。
    内置一套合法默认值（张三/13800138000/北京市海淀区/XX路1号），
    需要特殊数据时用 overrides 覆盖，例如：
        create_address(s, url, h, receiver_name="李四", is_default=True)
    """
    import time
    payload = {
        "receiver_name": "张三",
        "receiver_phone": "13800138000",
        "region": "北京市海淀区",
        "detail": "中关村大街1号",
    }
    payload.update(overrides)
    r = requests_session.post(f"{base_url}/addresses", headers=headers, json=payload)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return r.json()["data"]["id"]


def place_order_with_address(requests_session, base_url, headers,
                             product_id=1, qty=1, address_id=None):
    """加购 → 用指定地址下单，返回 (order_id, address_id)。
    传 address_id=None 时自动新建一条地址（全新用户场景用）。
    旧的 place_order 类 helper 下单必须带 address_id，用它最省事。"""
    if address_id is None:
        address_id = create_address(requests_session, base_url, headers)
    requests_session.post(f"{base_url}/cart", headers=headers,
                          json={"product_id": product_id, "quantity": qty})
    r = requests_session.post(f"{base_url}/orders", headers=headers,
                              json={"address_id": address_id})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return r.json()["data"]["order_id"], address_id


@pytest.fixture(scope="session")
def user_address_id(base_url, requests_session, register_user):
    """session 级共享用户（register_user）的默认收货地址 id。
    用 auth_headers 下单又不想每个用例单独造地址时，直接依赖它。"""
    _, token = register_user
    headers = {"Authorization": f"Bearer {token}"}
    return create_address(requests_session, base_url, headers)


@pytest.fixture
def address_factory(base_url, requests_session):
    """function 级地址工厂：返回 create_address 的偏函数，
    用法：aid = address_factory(headers, receiver_name="李四")"""
    from functools import partial
    return partial(create_address, requests_session, base_url)
