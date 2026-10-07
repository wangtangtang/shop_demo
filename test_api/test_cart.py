"""购物车 & 订单流程测试

========== 0915 追加：勾选结算（cart_item_ids）共 7 个测试函数 ==========
- 传勾选 id 子集下单：只结算勾选 2 件，total/订单条目正确，未勾选条目保留
- 不传 cart_item_ids：整辆购物车下单（老行为向后兼容）
- 空数组 → 400「请勾选要结算的商品」
- 越权：夹带别的用户的购物车条目 id → 400，订单数不增加、库存不变、不建单
- cart_item_ids 非数组 / 元素非整数 → 400
- 勾选条目含已下架商品 → 400

本文件原 8 条 + 0915 追加 7 个测试函数（非法类型 1 个参数化 6 条）= 共 19 条用例。
"""
import time

import pytest

from test_admin_products import admin_headers, create_product
from test_logistics import make_user
from conftest import create_address


def _fresh_user(requests_session, base_url):
    """注册一个全新用户 + 地址，返回 (headers, address_id)，购物车保证为空"""
    h = make_user(requests_session, base_url)
    aid = create_address(requests_session, base_url, h)
    return h, aid


_UNIQUE_SEQ = 0


def _unique_product(requests_session, base_url, ah, price, stock=20):
    """造一个唯一名商品（避免关键字/数据互相干扰），返回 (pid, price)。
    不能只用 time_ns：Windows Python 3.9 时钟分辨率约 15.6ms，连续调用
    可能取到同一纳秒值导致重名（断言"未勾选商品不在订单里"会误伤），
    因此叠加进程内自增序号保证真正唯一。"""
    global _UNIQUE_SEQ
    _UNIQUE_SEQ += 1
    pid = create_product(requests_session, base_url, ah,
                         name=f"勾选商品{time.time_ns()}_{_UNIQUE_SEQ}",
                         price=price, stock=stock).json()["data"]["id"]
    return pid, price


def _add_to_cart(requests_session, base_url, h, pid, qty=1):
    r = requests_session.post(f"{base_url}/cart", headers=h,
                              json={"product_id": pid, "quantity": qty})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    cart = requests_session.get(f"{base_url}/cart", headers=h).json()["data"]["items"]
    return next(i["id"] for i in cart if i["product_id"] == pid)


# ---------- 勾选结算：cart_item_ids ----------

def test_checkout_selected_items_only(requests_session, base_url):
    """加购 3 个不同商品，只传其中 2 个条目 id 下单：
    响应 total=两件之和；订单条目只有 2 件；购物车剩 1 件"""
    ah = admin_headers(requests_session, base_url)
    h, aid = _fresh_user(requests_session, base_url)
    p1, price1 = _unique_product(requests_session, base_url, ah, 10.5)
    p2, price2 = _unique_product(requests_session, base_url, ah, 20.0)
    p3, price3 = _unique_product(requests_session, base_url, ah, 7.25)
    name1 = _product_name(requests_session, base_url, p1)
    name2 = _product_name(requests_session, base_url, p2)
    name3 = _product_name(requests_session, base_url, p3)
    cid1 = _add_to_cart(requests_session, base_url, h, p1)
    cid2 = _add_to_cart(requests_session, base_url, h, p2)
    cid3 = _add_to_cart(requests_session, base_url, h, p3)

    r = requests_session.post(f"{base_url}/orders", headers=h,
                              json={"address_id": aid,
                                    "cart_item_ids": [cid1, cid3]})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    oid = r.json()["data"]["order_id"]
    assert abs(r.json()["data"]["total"] - (price1 + price3)) < 0.001

    # 订单详情：只有勾选的第 1、3 件，未勾选的第 2 件不在订单里
    detail = requests_session.get(f"{base_url}/orders/{oid}", headers=h).json()["data"]
    names = [it["product_name"] for it in detail["items"]]
    assert len(detail["items"]) == 2
    assert name1 in names and name3 in names
    assert name2 not in names

    # 购物车只剩未勾选的第 2 件
    cart = requests_session.get(f"{base_url}/cart", headers=h).json()["data"]["items"]
    assert len(cart) == 1
    assert cart[0]["id"] == cid2


def _product_name(requests_session, base_url, pid):
    return requests_session.get(f"{base_url}/products/{pid}").json()["data"]["name"]


def test_checkout_without_ids_keeps_legacy_all_cart(requests_session, base_url):
    """不传 cart_item_ids：维持老行为，整辆购物车全部下单并清空"""
    ah = admin_headers(requests_session, base_url)
    h, aid = _fresh_user(requests_session, base_url)
    p1, price1 = _unique_product(requests_session, base_url, ah, 11.0)
    p2, price2 = _unique_product(requests_session, base_url, ah, 13.0)
    _add_to_cart(requests_session, base_url, h, p1)
    _add_to_cart(requests_session, base_url, h, p2)

    r = requests_session.post(f"{base_url}/orders", headers=h,
                              json={"address_id": aid})  # 不带 cart_item_ids
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    assert abs(r.json()["data"]["total"] - (price1 + price2)) < 0.001
    cart = requests_session.get(f"{base_url}/cart", headers=h).json()["data"]["items"]
    assert cart == []


def test_checkout_empty_ids_400(requests_session, base_url):
    """传空数组 → 400 请勾选要结算的商品，且不清购物车、不建单"""
    ah = admin_headers(requests_session, base_url)
    h, aid = _fresh_user(requests_session, base_url)
    p1, _ = _unique_product(requests_session, base_url, ah, 9.9)
    _add_to_cart(requests_session, base_url, h, p1)
    orders_before = requests_session.get(f"{base_url}/orders", headers=h).json()["data"]["total"]

    r = requests_session.post(f"{base_url}/orders", headers=h,
                              json={"address_id": aid, "cart_item_ids": []})
    assert r.status_code == 400
    assert "勾选" in r.json()["msg"]

    orders_after = requests_session.get(f"{base_url}/orders", headers=h).json()["data"]["total"]
    assert orders_after == orders_before
    assert len(requests_session.get(f"{base_url}/cart", headers=h).json()["data"]["items"]) == 1


def test_checkout_ids_with_other_users_item_400(requests_session, base_url):
    """越权：把第二个用户购物车条目的 id 夹带进来 → 400 购物车商品不存在；
    不创建订单、不扣库存、自己的购物车条目原样保留"""
    ah = admin_headers(requests_session, base_url)
    h1, aid1 = _fresh_user(requests_session, base_url)
    h2, _ = _fresh_user(requests_session, base_url)
    p1, _ = _unique_product(requests_session, base_url, ah, 19.0, stock=10)
    p2, _ = _unique_product(requests_session, base_url, ah, 29.0, stock=10)
    cid1 = _add_to_cart(requests_session, base_url, h1, p1)
    cid_other = _add_to_cart(requests_session, base_url, h2, p2)  # 别人的条目

    stock_p1_before = requests_session.get(f"{base_url}/products/{p1}").json()["data"]["stock"]
    stock_p2_before = requests_session.get(f"{base_url}/products/{p2}").json()["data"]["stock"]
    orders_before = requests_session.get(f"{base_url}/orders", headers=h1).json()["data"]["total"]

    r = requests_session.post(f"{base_url}/orders", headers=h1,
                              json={"address_id": aid1,
                                    "cart_item_ids": [cid1, cid_other]})
    assert r.status_code == 400
    assert "购物车商品不存在" in r.json()["msg"]

    # 订单数不增加
    orders_after = requests_session.get(f"{base_url}/orders", headers=h1).json()["data"]["total"]
    assert orders_after == orders_before
    # 两件商品库存都不变（校验失败在扣库存之前）
    assert requests_session.get(f"{base_url}/products/{p1}").json()["data"]["stock"] == stock_p1_before
    assert requests_session.get(f"{base_url}/products/{p2}").json()["data"]["stock"] == stock_p2_before
    # 双方购物车都原样保留
    assert len(requests_session.get(f"{base_url}/cart", headers=h1).json()["data"]["items"]) == 1
    assert len(requests_session.get(f"{base_url}/cart", headers=h2).json()["data"]["items"]) == 1


@pytest.mark.parametrize("bad_ids", [
    "1,2",            # 字符串（不是数组）
    {"id": 1},        # 对象
    123,              # 整数
    [1, "2"],         # 数组里混字符串
    [1, None],        # 数组里混 null
    [1, True],        # 数组里混布尔（bool 不能当条目 id）
])
def test_checkout_ids_bad_type_400(requests_session, base_url, bad_ids):
    """cart_item_ids 不是整数数组 → 400"""
    ah = admin_headers(requests_session, base_url)
    h, aid = _fresh_user(requests_session, base_url)
    p1, _ = _unique_product(requests_session, base_url, ah, 5.0)
    _add_to_cart(requests_session, base_url, h, p1)

    r = requests_session.post(f"{base_url}/orders", headers=h,
                              json={"address_id": aid, "cart_item_ids": bad_ids})
    assert r.status_code == 400, r.text


def test_checkout_selected_deleted_product_400(requests_session, base_url):
    """勾选条目含已下架商品 → 400（造商品→加购→后台下架→下单）；
    下架会清掉该购物车条目，于是该 id 按“购物车商品不存在”拒绝，绝不建单"""
    ah = admin_headers(requests_session, base_url)
    h, aid = _fresh_user(requests_session, base_url)
    p1, _ = _unique_product(requests_session, base_url, ah, 8.0)
    p2, _ = _unique_product(requests_session, base_url, ah, 12.0)
    cid1 = _add_to_cart(requests_session, base_url, h, p1)
    cid2 = _add_to_cart(requests_session, base_url, h, p2)

    # 后台下架 p1（下架会把所有人购物车里这件商品清掉）
    d = requests_session.delete(f"{base_url}/admin/products/{p1}", headers=ah)
    assert d.status_code == 200 and d.json()["code"] == 0, d.text

    orders_before = requests_session.get(f"{base_url}/orders", headers=h).json()["data"]["total"]
    # cid1 已随下架被清理，夹着它下单必须 400，且不能把 cid2 单独结算掉
    r = requests_session.post(f"{base_url}/orders", headers=h,
                              json={"address_id": aid,
                                    "cart_item_ids": [cid1, cid2]})
    assert r.status_code == 400
    orders_after = requests_session.get(f"{base_url}/orders", headers=h).json()["data"]["total"]
    assert orders_after == orders_before
    cart = requests_session.get(f"{base_url}/cart", headers=h).json()["data"]["items"]
    assert [i["id"] for i in cart] == [cid2]



def test_add_to_cart(auth_headers, requests_session, base_url):
    resp = requests_session.post(f"{base_url}/cart",
                                 json={"product_id": 1, "quantity": 2},
                                 headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


def test_cart_requires_auth(requests_session, base_url):
    """未登录访问购物车应该 401"""
    resp = requests_session.get(f"{base_url}/cart")
    assert resp.status_code == 401


def test_list_cart(auth_headers, requests_session, base_url):
    # 先加一个商品
    requests_session.post(f"{base_url}/cart",
                          json={"product_id": 2, "quantity": 1},
                          headers=auth_headers)
    resp = requests_session.get(f"{base_url}/cart", headers=auth_headers)
    data = resp.json()
    assert data["code"] == 0
    assert "items" in data["data"]
    assert data["data"]["total"] > 0


def test_add_invalid_quantity(auth_headers, requests_session, base_url):
    resp = requests_session.post(f"{base_url}/cart",
                                 json={"product_id": 1, "quantity": 0},
                                 headers=auth_headers)
    assert resp.status_code == 400


def test_create_order(auth_headers, requests_session, base_url, user_address_id):
    # 保证购物车有东西
    requests_session.post(f"{base_url}/cart",
                          json={"product_id": 3, "quantity": 1},
                          headers=auth_headers)
    resp = requests_session.post(f"{base_url}/orders", headers=auth_headers,
                                 json={"address_id": user_address_id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert data["data"]["total"] > 0


def test_order_clears_cart(auth_headers, requests_session, base_url):
    # 下单后购物车应该清空
    resp = requests_session.get(f"{base_url}/cart", headers=auth_headers)
    assert resp.json()["data"]["items"] == []


def test_list_orders(auth_headers, requests_session, base_url):
    resp = requests_session.get(f"{base_url}/orders", headers=auth_headers)
    data = resp.json()
    assert data["code"] == 0
    # 订单接口已升级为分页结构：data.items 是订单列表，data.total 是总数
    assert isinstance(data["data"]["items"], list)
    assert data["data"]["total"] >= 1
    assert data["data"]["page"] == 1


def test_cart_multi_image_product_uses_first_image(auth_headers, requests_session, base_url):
    """多图商品加入购物车：返回的 image 必须是第一张图，不能把 "a.png,b.png" 整个串当文件名"""
    import time as _time
    from test_admin_products import admin_headers, create_product

    ah = admin_headers(requests_session, base_url)
    suffix = _time.time_ns()
    pid = create_product(requests_session, base_url, ah,
                         name=f"多图购物车{suffix}", price=3.5, stock=5,
                         image=f"cart_a_{suffix}.png,cart_b_{suffix}.png").json()["data"]["id"]
    requests_session.post(f"{base_url}/cart",
                          json={"product_id": pid, "quantity": 1},
                          headers=auth_headers)
    items = requests_session.get(f"{base_url}/cart",
                                 headers=auth_headers).json()["data"]["items"]
    mine = next(i for i in items if i["product_id"] == pid)
    assert mine["image"] == f"cart_a_{suffix}.png", "多图商品购物车缩略图应取第一张"
    assert "," not in mine["image"], "image 不能是逗号分隔串，否则前端图片 404"
