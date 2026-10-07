"""物流信息 + 下单地址校验 专项测试
=====================================
覆盖点：
- 发货接口改造：未支付订单不能发货（400）；已支付订单发货成功 →
  状态 shipped 且返回物流公司/运单号；缺物流公司/运单号 400；重复发货 400
- 发货后顾客订单详情包含物流信息和 4 条模拟轨迹（发货即生成完整链路
  已揽收/运输中/派送中/已签收）；未发货订单详情无轨迹
- 确认收货只改订单状态，不再追加物流轨迹（不重复写「已签收」）
- 下单必须带 address_id（不传 400，提示「请选择收货地址」）；
  带他人 address_id → 404
- 下单成功后订单详情含地址快照，且之后修改地址簿不影响订单快照
"""
import time
import pytest

from conftest import create_address


def make_user(requests_session, base_url):
    """注册全新普通用户，返回 headers"""
    username = f"log_{int(time.time() * 1000)}"
    requests_session.post(f"{base_url}/register",
                          json={"username": username, "password": "123456"})
    r = requests_session.post(f"{base_url}/login",
                              json={"username": username, "password": "123456"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def admin_headers(requests_session, base_url):
    r = requests_session.post(f"{base_url}/login",
                              json={"username": "admin", "password": "admin123"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def paid_order(requests_session, base_url, headers, product_id=1, **addr_kw):
    """建地址 → 加购 → 下单 → 付款（模拟回调），返回 order_id"""
    aid = create_address(requests_session, base_url, headers, **addr_kw)
    requests_session.post(f"{base_url}/cart", headers=headers,
                          json={"product_id": product_id, "quantity": 1})
    r = requests_session.post(f"{base_url}/orders", headers=headers,
                              json={"address_id": aid})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    oid = r.json()["data"]["order_id"]
    requests_session.post(f"{base_url}/orders/{oid}/pay", headers=headers,
                          json={"channel": "wechat"})
    requests_session.post(f"{base_url}/pay/mock-notify", json={"order_id": oid})
    return oid, aid


def ship_body(**kw):
    body = {"logistics_company": "顺丰速运", "tracking_no": "SF" + str(int(time.time() * 1000))}
    body.update(kw)
    return body


# ---------- 发货接口 ----------

def test_ship_pending_order_400(auth_headers, requests_session, base_url, user_address_id):
    """未支付（pending）订单不能发货 → 400"""
    ah = admin_headers(requests_session, base_url)
    requests_session.post(f"{base_url}/cart", headers=auth_headers,
                          json={"product_id": 2, "quantity": 1})
    r = requests_session.post(f"{base_url}/orders", headers=auth_headers,
                              json={"address_id": user_address_id})
    oid = r.json()["data"]["order_id"]
    # 不付款直接发货
    r = requests_session.post(f"{base_url}/admin/orders/{oid}/ship", headers=ah,
                              json=ship_body())
    assert r.status_code == 400


def test_ship_paid_order_ok(requests_session, base_url):
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid, _ = paid_order(requests_session, base_url, h, product_id=3)

    body = ship_body(logistics_company="中通快递", tracking_no="ZT5566778899")
    r = requests_session.post(f"{base_url}/admin/orders/{oid}/ship", headers=ah,
                              json=body)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    data = r.json()["data"]
    assert data["logistics_company"] == "中通快递"
    assert data["tracking_no"] == "ZT5566778899"

    # 订单状态变为 shipped
    r = requests_session.get(f"{base_url}/orders/{oid}", headers=h)
    assert r.status_code == 200
    order = r.json()["data"]
    assert order["status"] == "shipped"
    assert order["logistics_company"] == "中通快递"
    assert order["tracking_no"] == "ZT5566778899"
    assert order["shipped_at"] is not None


def test_ship_missing_company_400(requests_session, base_url):
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid, _ = paid_order(requests_session, base_url, h, product_id=4)

    r = requests_session.post(f"{base_url}/admin/orders/{oid}/ship", headers=ah,
                              json={"tracking_no": "SF0000000001"})
    assert r.status_code == 400
    assert r.json()["code"] == 400


def test_ship_missing_tracking_no_400(requests_session, base_url):
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid, _ = paid_order(requests_session, base_url, h, product_id=5)

    r = requests_session.post(f"{base_url}/admin/orders/{oid}/ship", headers=ah,
                              json={"logistics_company": "顺丰速运"})
    assert r.status_code == 400
    assert r.json()["code"] == 400


def test_ship_twice_400(requests_session, base_url):
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid, _ = paid_order(requests_session, base_url, h, product_id=1)

    r1 = requests_session.post(f"{base_url}/admin/orders/{oid}/ship", headers=ah,
                               json=ship_body())
    assert r1.status_code == 200
    # 重复发货 → 400
    r2 = requests_session.post(f"{base_url}/admin/orders/{oid}/ship", headers=ah,
                               json=ship_body(tracking_no="SF9999999999"))
    assert r2.status_code == 400


# ---------- 订单详情：物流轨迹 ----------

def test_order_detail_has_tracks_after_ship(requests_session, base_url):
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid, _ = paid_order(requests_session, base_url, h, product_id=2)
    requests_session.post(f"{base_url}/admin/orders/{oid}/ship", headers=ah,
                          json=ship_body())

    r = requests_session.get(f"{base_url}/orders/{oid}", headers=h)
    assert r.status_code == 200
    order = r.json()["data"]
    tracks = order["tracks"]
    assert len(tracks) == 4, "发货后应自动生成 4 条模拟物流轨迹（完整链路）"
    statuses = [t["status"] for t in tracks]
    assert statuses == ["已揽收", "运输中", "派送中", "已签收"], "轨迹按 track_time 正序"
    for t in tracks:
        assert t["track_time"], "每条轨迹都应有时间"
        assert t["info"], "每条轨迹都应有描述"


def test_tracks_complete_after_ship_and_confirm(requests_session, base_url):
    """发货时即生成 4 条完整轨迹（含已签收）；确认收货只改订单状态，
    不重复追加轨迹：confirm 后轨迹仍为 4 条，最后一条仍是「已签收」"""
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid, _ = paid_order(requests_session, base_url, h, product_id=4)
    requests_session.post(f"{base_url}/admin/orders/{oid}/ship", headers=ah,
                          json=ship_body())

    # 发货后：已是 4 条完整轨迹
    before = requests_session.get(f"{base_url}/orders/{oid}", headers=h).json()["data"]
    before_statuses = [t["status"] for t in before["tracks"]]
    assert len(before_statuses) == 4
    assert before_statuses == ["已揽收", "运输中", "派送中", "已签收"]

    # 用户确认收货
    r = requests_session.post(f"{base_url}/orders/{oid}/action", headers=h,
                              json={"action": "confirm"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text

    detail = requests_session.get(f"{base_url}/orders/{oid}", headers=h).json()["data"]
    assert detail["status"] == "completed"
    statuses = [t["status"] for t in detail["tracks"]]
    assert len(statuses) == 4, f"确认收货不应追加轨迹，仍应是4条，实际{len(statuses)}"
    assert statuses == ["已揽收", "运输中", "派送中", "已签收"]
    assert statuses[-1] == "已签收", "正序最后一条（最新）应为已签收"


def test_unshipped_order_detail_has_no_tracks(requests_session, base_url):
    h = make_user(requests_session, base_url)
    oid, _ = paid_order(requests_session, base_url, h, product_id=3)
    # 已支付但未发货
    r = requests_session.get(f"{base_url}/orders/{oid}", headers=h)
    assert r.status_code == 200
    order = r.json()["data"]
    assert order["tracks"] == [], "未发货订单不应有物流轨迹"
    assert order["logistics_company"] in ("", None)
    assert order["shipped_at"] is None


# ---------- 下单地址校验 ----------

def test_create_order_without_address_400(auth_headers, requests_session, base_url):
    """下单不带 address_id → 400，提示请选择收货地址"""
    requests_session.post(f"{base_url}/cart", headers=auth_headers,
                          json={"product_id": 4, "quantity": 1})
    r = requests_session.post(f"{base_url}/orders", headers=auth_headers, json={})
    assert r.status_code == 400
    assert "地址" in r.json()["msg"]


def test_create_order_with_others_address_404(requests_session, base_url):
    """带别人的 address_id 下单 → 404（不暴露地址存在性）"""
    h1 = make_user(requests_session, base_url)
    h2 = make_user(requests_session, base_url)
    others_aid = create_address(requests_session, base_url, h1)

    requests_session.post(f"{base_url}/cart", headers=h2,
                          json={"product_id": 5, "quantity": 1})
    r = requests_session.post(f"{base_url}/orders", headers=h2,
                              json={"address_id": others_aid})
    assert r.status_code == 404


# ---------- 地址快照 ----------

def test_order_keeps_address_snapshot(requests_session, base_url):
    """下单后详情含地址快照；之后修改地址簿，订单快照内容不变"""
    h = make_user(requests_session, base_url)
    aid = create_address(requests_session, base_url, h,
                         receiver_name="快照张三", receiver_phone="13800138000",
                         region="北京市朝阳区", detail="快照路100号")
    requests_session.post(f"{base_url}/cart", headers=h,
                          json={"product_id": 1, "quantity": 1})
    r = requests_session.post(f"{base_url}/orders", headers=h,
                              json={"address_id": aid})
    assert r.status_code == 200, r.text
    oid = r.json()["data"]["order_id"]

    # 详情里的快照是下单时的地址内容
    r = requests_session.get(f"{base_url}/orders/{oid}", headers=h)
    snap = r.json()["data"]["address_snapshot"]
    assert snap is not None
    assert snap["receiver_name"] == "快照张三"
    assert snap["receiver_phone"] == "13800138000"
    assert snap["region"] == "北京市朝阳区"
    assert snap["detail"] == "快照路100号"

    # 之后把地址簿里的地址改成完全不同的内容
    r = requests_session.put(f"{base_url}/addresses/{aid}", headers=h, json={
        "receiver_name": "改后李四", "receiver_phone": "13900139000",
        "region": "广州市天河区", "detail": "改后大道200号"
    })
    assert r.status_code == 200, r.text

    # 历史订单快照不受影响
    r = requests_session.get(f"{base_url}/orders/{oid}", headers=h)
    snap2 = r.json()["data"]["address_snapshot"]
    assert snap2["receiver_name"] == "快照张三"
    assert snap2["region"] == "北京市朝阳区"
    assert snap2["detail"] == "快照路100号"
