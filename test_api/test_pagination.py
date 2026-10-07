"""分页功能测试（我的订单 + 后台订单）
========================================
分页是列表类接口的标配，面试和论文里可以从「接口设计 +
边界值 + 参数校验 + 数据一致性」四个层面讲：

1. 正常分页：第 1 页条数 = per_page，total/total_pages 计算正确
2. 排序正确性：订单按创建时间倒序，最新订单在第 1 页
3. 边界值：最后一页条数 = total - (total_pages-1)*per_page；
   页码越界（page=999）不报错，返回空 items
4. 参数校验：page/per_page 非法（0、负数、字母、per_page>50）应返回 400
5. 数据一致性：各页订单号不重复，合计条数 = total
6. 筛选 + 分页组合：后台按状态筛选后分页，页内订单状态全部正确
"""
import time
import pytest


def make_user(requests_session, base_url):
    """注册全新用户，返回 headers"""
    username = f"page_{int(time.time() * 1000)}"
    requests_session.post(f"{base_url}/register",
                          json={"username": username, "password": "123456"})
    resp = requests_session.post(f"{base_url}/login",
                                 json={"username": username, "password": "123456"})
    token = resp.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


def order_n(requests_session, base_url, headers, product_id):
    """下一单（建地址 → 加购 → 下单），返回订单 id"""
    from conftest import create_address
    address_id = create_address(requests_session, base_url, headers)
    requests_session.post(f"{base_url}/cart",
                          json={"product_id": product_id, "quantity": 1},
                          headers=headers)
    resp = requests_session.post(f"{base_url}/orders", headers=headers,
                                 json={"address_id": address_id})
    assert resp.status_code == 200
    return resp.json()["data"]["order_id"]


def admin_headers(requests_session, base_url):
    resp = requests_session.post(f"{base_url}/login",
                                 json={"username": "admin", "password": "admin123"})
    token = resp.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


# ---------- 我的订单分页 ----------
def test_my_orders_first_page(auth_headers, requests_session, base_url):
    """造 6 单，per_page=5：第 1 页 5 条，结构字段齐全"""
    for i in range(6):
        order_n(requests_session, base_url, auth_headers,
                product_id=(i % 5) + 1)
    r = requests_session.get(f"{base_url}/orders?page=1&per_page=5",
                             headers=auth_headers)
    assert r.status_code == 200
    pd = r.json()["data"]
    assert len(pd["items"]) == 5
    assert pd["total"] >= 6
    assert pd["per_page"] == 5
    assert pd["total_pages"] == (pd["total"] + 4) // 5
    assert "page" in pd and pd["page"] == 1


def test_pagination_desc_order(auth_headers, requests_session, base_url):
    """订单按创建时间倒序：第 1 页第一条是最新订单"""
    oid = order_n(requests_session, base_url, auth_headers, product_id=1)
    r = requests_session.get(f"{base_url}/orders?page=1&per_page=5",
                             headers=auth_headers)
    first_id = r.json()["data"]["items"][0]["id"]
    assert first_id == oid


def test_pagination_last_page(auth_headers, requests_session, base_url):
    """边界值：最后一页条数 = 总数对 per_page 取模的余数（不为 0 时）"""
    r = requests_session.get(f"{base_url}/orders?page=1&per_page=5",
                             headers=auth_headers)
    pd = r.json()["data"]
    if pd["total"] % 5 == 0:
        pytest.skip("当前总数正好被 5 整除，换 per_page 重跑即可覆盖")
    last = requests_session.get(
        f"{base_url}/orders?page={pd['total_pages']}&per_page=5",
        headers=auth_headers).json()["data"]
    assert len(last["items"]) == pd["total"] % 5


def test_pagination_page_out_of_range(auth_headers, requests_session, base_url):
    """页码越界（999 页）：不报错，返回空 items，total_pages 正常"""
    r = requests_session.get(f"{base_url}/orders?page=999&per_page=5",
                             headers=auth_headers)
    assert r.status_code == 200
    pd = r.json()["data"]
    assert pd["items"] == []
    assert pd["total"] >= 1


def test_pagination_no_duplicate_and_sum_matches_total(
        auth_headers, requests_session, base_url):
    """数据一致性：各页订单号不重复，翻完全部页条数合计 == total"""
    seen = []
    page = 1
    while True:
        pd = requests_session.get(
            f"{base_url}/orders?page={page}&per_page=5",
            headers=auth_headers).json()["data"]
        if not pd["items"]:
            assert page > pd["total_pages"]  # 翻到总数页之后才为空
            break
        for o in pd["items"]:
            assert o["id"] not in seen  # 同一条订单不能出现在两页
            seen.append(o["id"])
        page += 1
    assert len(seen) == pd["total"]


@pytest.mark.parametrize("params", [
    "page=0",                 # 页码从 1 开始
    "page=-1",                # 负数
    "page=abc",               # 非数字
    "per_page=0",             # 每页 0 条没意义
    "per_page=51",            # 超过上限 50（防止一次拉全表）
    "per_page=100",
])
def test_pagination_bad_params(auth_headers, requests_session, base_url, params):
    """参数校验：非法分页参数必须 400，不能 500 也不能静默忽略"""
    r = requests_session.get(f"{base_url}/orders?{params}",
                             headers=auth_headers)
    assert r.status_code == 400


# ---------- 后台订单分页 + 筛选组合 ----------
def test_admin_orders_paginated(auth_headers, requests_session, base_url):
    """后台订单接口同样是分页结构（老版本是全量数组，防回归）"""
    ah = admin_headers(requests_session, base_url)
    r = requests_session.get(f"{base_url}/admin/orders?page=1&per_page=5",
                             headers=ah)
    assert r.status_code == 200
    pd = r.json()["data"]
    assert len(pd["items"]) <= 5
    assert pd["total"] >= 1
    assert pd["total_pages"] >= 1


def test_admin_filter_with_pagination(requests_session, base_url):
    """筛选 + 分页组合：只看待发货(pending)，页内订单状态必须全是 pending，
    且筛选结果的 total 不大于全部订单的 total"""
    ah = admin_headers(requests_session, base_url)
    r_all = requests_session.get(f"{base_url}/admin/orders?page=1&per_page=5",
                                 headers=ah).json()["data"]
    r_pending = requests_session.get(
        f"{base_url}/admin/orders?status=pending&page=1&per_page=5",
        headers=ah).json()["data"]
    assert r_pending["total"] <= r_all["total"]
    for o in r_pending["items"]:
        assert o["status"] == "pending"
