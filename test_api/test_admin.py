"""后台管理 & 订单状态流转测试

覆盖内容：
1. 管理员登录 / 权限隔离（普通顾客不能调后台接口）
2. 订单状态机：下单(待支付) → 付款(待发货) → 管理员发货(已发货) → 收货(已完成)
3. 取消订单后库存退回
4. 非法流转被拒绝（如已发货的订单不能再取消）
5. 后台商品的增、改、删
"""
import time
import pytest


# ---------- 公共工具 ----------
def make_user(requests_session, base_url):
    """注册一个全新的普通用户，返回 (headers, user_id)"""
    username = f"flow_{int(time.time() * 1000)}"
    requests_session.post(f"{base_url}/register",
                          json={"username": username, "password": "123456"})
    resp = requests_session.post(f"{base_url}/login",
                                 json={"username": username, "password": "123456"})
    token = resp.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


def admin_headers(requests_session, base_url):
    """管理员账号登录（admin/admin123 由 seed.py 自动创建）"""
    resp = requests_session.post(f"{base_url}/login",
                                 json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, "管理员登录失败，检查 seed.py 是否创建了 admin"
    token = resp.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


def place_order(requests_session, base_url, headers, product_id=1, qty=1):
    """走完整下单流程：建地址 → 加购 → 下单，返回订单 id"""
    from conftest import create_address
    address_id = create_address(requests_session, base_url, headers)
    requests_session.post(f"{base_url}/cart",
                          json={"product_id": product_id, "quantity": qty},
                          headers=headers)
    resp = requests_session.post(f"{base_url}/orders", headers=headers,
                                 json={"address_id": address_id})
    assert resp.status_code == 200
    return resp.json()["data"]["order_id"]


def get_stock(requests_session, base_url, product_id):
    resp = requests_session.get(f"{base_url}/products/{product_id}")
    return resp.json()["data"]["stock"]


# ---------- 1. 权限 ----------
def test_admin_login_returns_is_admin(requests_session, base_url):
    resp = requests_session.post(f"{base_url}/login",
                                 json={"username": "admin", "password": "admin123"})
    assert resp.json()["data"]["is_admin"] is True


def test_normal_user_is_not_admin(auth_headers, requests_session, base_url):
    resp = requests_session.post(f"{base_url}/login",
                                 json={"username": "nouser", "password": "x"})
    # 不存在的用户登录失败；用已注册用户验证 is_admin=False
    # auth_headers 对应的 fixture 用户已登录过，直接看后台接口应 403
    resp = requests_session.get(f"{base_url}/admin/orders", headers=auth_headers)
    assert resp.status_code == 403


def test_admin_orders_requires_login(requests_session, base_url):
    """未登录访问后台订单列表应该 401"""
    resp = requests_session.get(f"{base_url}/admin/orders")
    assert resp.status_code == 401


# ---------- 2. 订单状态机 ----------
def test_new_order_is_pending(auth_headers, requests_session, base_url):
    """新订单默认状态应该是 pending(待支付)，不再是直接 paid"""
    oid = place_order(requests_session, base_url, auth_headers, product_id=2)
    resp = requests_session.get(f"{base_url}/orders", headers=auth_headers)
    order = next(o for o in resp.json()["data"]["items"] if o["id"] == oid)
    assert order["status"] == "pending"
    assert order["status_text"] == "待支付"
    assert "pay" in order["actions"]


def test_full_order_flow(auth_headers, requests_session, base_url):
    """完整链路：付款 → 管理员发货 → 确认收货"""
    ah = admin_headers(requests_session, base_url)
    oid = place_order(requests_session, base_url, auth_headers, product_id=3)

    # 顾客付款
    r = requests_session.post(f"{base_url}/orders/{oid}/action",
                              json={"action": "pay"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "paid"

    # 管理员发货（填物流公司 + 运单号）
    r = requests_session.post(f"{base_url}/admin/orders/{oid}/ship", headers=ah,
                              json={"logistics_company": "顺丰速运",
                                    "tracking_no": "SF1234567890"})
    assert r.status_code == 200

    # 顾客确认收货
    r = requests_session.post(f"{base_url}/orders/{oid}/action",
                              json={"action": "confirm"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "completed"


def test_cannot_ship_pending_order(auth_headers, requests_session, base_url):
    """待支付的订单管理员不能发货（状态机非法流转）"""
    ah = admin_headers(requests_session, base_url)
    oid = place_order(requests_session, base_url, auth_headers, product_id=4)
    r = requests_session.post(f"{base_url}/admin/orders/{oid}/ship", headers=ah)
    assert r.status_code == 400


def test_cancel_restores_stock(auth_headers, requests_session, base_url):
    """取消订单后库存应该退回去"""
    headers = make_user(requests_session, base_url)
    product_id = 5
    stock_before = get_stock(requests_session, base_url, product_id)

    oid = place_order(requests_session, base_url, headers, product_id=product_id, qty=2)
    stock_after_order = get_stock(requests_session, base_url, product_id)
    assert stock_after_order == stock_before - 2

    r = requests_session.post(f"{base_url}/orders/{oid}/action",
                              json={"action": "cancel"}, headers=headers)
    assert r.status_code == 200
    stock_after_cancel = get_stock(requests_session, base_url, product_id)
    assert stock_after_cancel == stock_before


def test_shipped_order_cannot_cancel(auth_headers, requests_session, base_url):
    """已发货的订单不能取消"""
    ah = admin_headers(requests_session, base_url)
    oid = place_order(requests_session, base_url, auth_headers, product_id=2)
    requests_session.post(f"{base_url}/orders/{oid}/action",
                          json={"action": "pay"}, headers=auth_headers)
    requests_session.post(f"{base_url}/admin/orders/{oid}/ship", headers=ah,
                          json={"logistics_company": "中通快递",
                                "tracking_no": "ZT9876543210"})

    r = requests_session.post(f"{base_url}/orders/{oid}/action",
                              json={"action": "cancel"}, headers=auth_headers)
    assert r.status_code == 400


def test_user_cannot_touch_others_order(auth_headers, requests_session, base_url):
    """A 用户不能操作 B 用户的订单（越权校验）"""
    other = make_user(requests_session, base_url)
    oid = place_order(requests_session, base_url, other, product_id=1)
    # auth_headers 是另一个用户，去支付别人的订单应 404
    r = requests_session.post(f"{base_url}/orders/{oid}/action",
                              json={"action": "pay"}, headers=auth_headers)
    assert r.status_code == 404


# ---------- 3. 后台商品管理 ----------
def test_admin_product_crud(auth_headers, requests_session, base_url):
    ah = admin_headers(requests_session, base_url)
    suffix = int(time.time())

    # 新增（带图片文件名）
    r = requests_session.post(f"{base_url}/admin/products", headers=ah, json={
        "name": f"测试商品{suffix}", "price": 66.6, "stock": 10,
        "description": "后台添加的", "image": f"test_{suffix}.png"
    })
    assert r.status_code == 200
    pid = r.json()["data"]["id"]

    # 商品接口应返回 image 字段，且值就是提交的文件名
    resp = requests_session.get(f"{base_url}/products/{pid}").json()["data"]
    assert resp["image"] == f"test_{suffix}.png"

    # 修改（价格/库存/图片都能改）
    r = requests_session.put(f"{base_url}/admin/products/{pid}", headers=ah,
                             json={"price": 88.8, "stock": 20, "image": f"new_{suffix}.png"})
    assert r.status_code == 200
    resp = requests_session.get(f"{base_url}/products/{pid}").json()["data"]
    assert resp["price"] == 88.8
    assert resp["stock"] == 20
    assert resp["image"] == f"new_{suffix}.png"

    # 删除
    r = requests_session.delete(f"{base_url}/admin/products/{pid}", headers=ah)
    assert r.status_code == 200
    assert requests_session.get(f"{base_url}/products/{pid}").status_code == 404


def test_normal_user_cannot_create_product(auth_headers, requests_session, base_url):
    """普通顾客不能新增商品"""
    r = requests_session.post(f"{base_url}/admin/products", headers=auth_headers,
                              json={"name": "黑客商品", "price": 1, "stock": 1})
    assert r.status_code == 403


def test_create_product_invalid_price(auth_headers, requests_session, base_url):
    ah = admin_headers(requests_session, base_url)
    r = requests_session.post(f"{base_url}/admin/products", headers=ah,
                              json={"name": "坏商品", "price": -5, "stock": 1})
    assert r.status_code == 400


def test_product_image_field(auth_headers, requests_session, base_url):
    """商品列表/详情都应返回 image 字段（图片与商品 id 解耦）"""
    resp = requests_session.get(f"{base_url}/products",
                                params={"per_page": 50}).json()
    products = resp["data"]["items"]  # 列表已分页：商品在 data.items
    assert products, "商品列表为空，检查 seed.py 是否灌了初始商品"
    for p in products:
        assert "image" in p, "商品接口缺少 image 字段"
        assert p["image"], f"商品 {p['name']} 的图片文件名为空"
    # 详情接口同样返回
    pid = products[0]["id"]
    detail = requests_session.get(f"{base_url}/products/{pid}").json()["data"]
    assert detail["image"] == products[0]["image"]
