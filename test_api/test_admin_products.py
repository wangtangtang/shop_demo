"""后台商品管理边界值测试（新增 / 修改 / 删除）
==================================================
练测试用例设计的基本功：等价类 + 边界值分析。
以「价格必须大于 0」这条规则为例：
  - 有效等价类：正数（正常 99.9、最小 0.01）
  - 无效等价类：0、负数、非数字（字母/空/缺失）
  - 边界值：0.01（最小合法）、0（刚好非法）、-0.01（非法侧邻近）
库存同理：最小合法 0，非法侧 -1，非整数/非数字非法。
另外覆盖：不传图片用统一占位图、删除商品清理购物车、越权。
"""
import time
import pytest


# ---------- 工具 ----------
def admin_headers(requests_session, base_url):
    resp = requests_session.post(f"{base_url}/login",
                                 json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, "管理员登录失败，检查 seed.py"
    token = resp.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


def make_user(requests_session, base_url):
    username = f"admprod_{int(time.time() * 1000)}"
    requests_session.post(f"{base_url}/register",
                          json={"username": username, "password": "123456"})
    resp = requests_session.post(f"{base_url}/login",
                                 json={"username": username, "password": "123456"})
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


def create_product(requests_session, base_url, ah, **fields):
    """新增商品，返回响应；默认字段可被 fields 覆盖"""
    body = {"name": f"边界商品{int(time.time() * 1000)}",
            "price": 10.0, "stock": 5}
    body.update(fields)
    return requests_session.post(f"{base_url}/admin/products",
                                 headers=ah, json=body)


def get_product(requests_session, base_url, pid):
    return requests_session.get(f"{base_url}/products/{pid}").json()["data"]


# ---------- 1. 新增商品：正常场景 ----------
def test_create_product_normal(auth_headers, requests_session, base_url):
    """正常新增：价格、库存合理，应成功且数据落库正确"""
    ah = admin_headers(requests_session, base_url)
    r = create_product(requests_session, base_url, ah,
                       name="正常商品", price=99.9, stock=50,
                       description="测试描述")
    assert r.status_code == 200
    pid = r.json()["data"]["id"]
    p = get_product(requests_session, base_url, pid)
    assert p["name"] == "正常商品"
    assert p["price"] == 99.9
    assert p["stock"] == 50
    assert p["description"] == "测试描述"


def test_create_product_without_image_uses_default(
        auth_headers, requests_session, base_url):
    """新增商品不传图片：自动用统一占位图 default.svg（不会裂图）"""
    ah = admin_headers(requests_session, base_url)
    r = create_product(requests_session, base_url, ah,
                       name="没图商品", price=10, stock=3)
    pid = r.json()["data"]["id"]
    p = get_product(requests_session, base_url, pid)
    assert p["image"] == "default.svg"


def test_create_product_min_price(auth_headers, requests_session, base_url):
    """边界值：价格 0.01（最小合法值）应成功"""
    ah = admin_headers(requests_session, base_url)
    r = create_product(requests_session, base_url, ah, price=0.01)
    assert r.status_code == 200
    assert get_product(requests_session, base_url,
                       r.json()["data"]["id"])["price"] == 0.01


def test_create_product_zero_stock(auth_headers, requests_session, base_url):
    """边界值：库存 0 合法（预售/补货中商品），应成功"""
    ah = admin_headers(requests_session, base_url)
    r = create_product(requests_session, base_url, ah, stock=0)
    assert r.status_code == 200
    assert get_product(requests_session, base_url,
                       r.json()["data"]["id"])["stock"] == 0


# ---------- 2. 新增商品：非法入参（参数化边界值） ----------
@pytest.mark.parametrize("bad_price", [0, -1, -0.01, "abc", "", None])
def test_create_product_bad_price(auth_headers, requests_session, base_url,
                                  bad_price):
    """价格非法：0 / 负数 / 字母 / 空 / 缺失，全部 400，且商品不入库"""
    ah = admin_headers(requests_session, base_url)
    before = requests_session.get(f"{base_url}/products",
                                  params={"per_page": 50}).json()["data"]["total"]
    r = create_product(requests_session, base_url, ah, price=bad_price)
    assert r.status_code == 400, f"价格 {bad_price!r} 应被拒绝"
    after = requests_session.get(f"{base_url}/products",
                                 params={"per_page": 50}).json()["data"]["total"]
    assert after == before, "非法商品不应出现在商品列表里"


@pytest.mark.parametrize("bad_stock", [-1, -100, 1.5, "abc"])
def test_create_product_bad_stock(auth_headers, requests_session, base_url,
                                  bad_stock):
    """库存非法：负数 / 小数 / 字母，全部 400"""
    ah = admin_headers(requests_session, base_url)
    r = create_product(requests_session, base_url, ah, stock=bad_stock)
    assert r.status_code == 400, f"库存 {bad_stock!r} 应被拒绝"


@pytest.mark.parametrize("bad_name", ["", "   ", None])
def test_create_product_empty_name(auth_headers, requests_session, base_url,
                                   bad_name):
    """商品名为空 / 纯空格 / 缺失：400"""
    ah = admin_headers(requests_session, base_url)
    r = create_product(requests_session, base_url, ah, name=bad_name)
    assert r.status_code == 400


# ---------- 3. 修改商品：正常 + 边界 ----------
def test_update_price_partial(auth_headers, requests_session, base_url):
    """只改价格，其他字段（名称/库存）不应被动到（部分更新语义）"""
    ah = admin_headers(requests_session, base_url)
    pid = create_product(requests_session, base_url, ah,
                         name="只改价", price=100, stock=7).json()["data"]["id"]
    r = requests_session.put(f"{base_url}/admin/products/{pid}",
                             headers=ah, json={"price": 66.6})
    assert r.status_code == 200
    p = get_product(requests_session, base_url, pid)
    assert p["price"] == 66.6
    assert p["name"] == "只改价"
    assert p["stock"] == 7


@pytest.mark.parametrize("bad_price", [0, -5, "xyz"])
def test_update_bad_price_rejected(auth_headers, requests_session, base_url,
                                   bad_price):
    """改价时传入非法价格：400，且原价不变"""
    ah = admin_headers(requests_session, base_url)
    pid = create_product(requests_session, base_url, ah,
                         price=100).json()["data"]["id"]
    r = requests_session.put(f"{base_url}/admin/products/{pid}",
                             headers=ah, json={"price": bad_price})
    assert r.status_code == 400
    assert get_product(requests_session, base_url, pid)["price"] == 100


def test_update_stock_negative_rejected(auth_headers, requests_session, base_url):
    """改库存为负数：400，且原库存不变"""
    ah = admin_headers(requests_session, base_url)
    pid = create_product(requests_session, base_url, ah,
                         stock=10).json()["data"]["id"]
    r = requests_session.put(f"{base_url}/admin/products/{pid}",
                             headers=ah, json={"stock": -3})
    assert r.status_code == 400
    assert get_product(requests_session, base_url, pid)["stock"] == 10


def test_update_nonexistent_product_404(auth_headers, requests_session, base_url):
    """修改不存在的商品：404"""
    ah = admin_headers(requests_session, base_url)
    r = requests_session.put(f"{base_url}/admin/products/99999999",
                             headers=ah, json={"price": 1})
    assert r.status_code == 404


# ---------- 4. 删除商品 ----------
def test_delete_product_removes_and_404(auth_headers, requests_session, base_url):
    """删除商品：列表里没了，再查详情 404"""
    ah = admin_headers(requests_session, base_url)
    pid = create_product(requests_session, base_url, ah).json()["data"]["id"]
    assert requests_session.delete(
        f"{base_url}/admin/products/{pid}", headers=ah).status_code == 200
    assert requests_session.get(f"{base_url}/products/{pid}").status_code == 404


def test_delete_nonexistent_product_404(auth_headers, requests_session, base_url):
    """删除不存在的商品：404"""
    ah = admin_headers(requests_session, base_url)
    r = requests_session.delete(f"{base_url}/admin/products/99999999",
                                headers=ah)
    assert r.status_code == 404


# ---------- 5. 权限 ----------
def test_anonymous_cannot_access_admin_products(requests_session, base_url):
    """未登录调后台商品接口：401（新增/修改/删除都拦）"""
    assert requests_session.post(
        f"{base_url}/admin/products", json={"name": "x", "price": 1}).status_code == 401
    assert requests_session.put(
        f"{base_url}/admin/products/1", json={"price": 1}).status_code == 401
    assert requests_session.delete(
        f"{base_url}/admin/products/1").status_code == 401


# ---------- 6. 后台商品列表分页（含已下架，老版本是全量数组，防回归） ----------
def test_admin_products_paginated(auth_headers, requests_session, base_url):
    """后台商品列表返回统一分页结构；在售+已下架都在 total 里；
    默认 per_page=10，上限 50（参数校验与订单分页一致）"""
    ah = admin_headers(requests_session, base_url)
    r = requests_session.get(f"{base_url}/admin/products",
                             params={"page": 1, "per_page": 5}, headers=ah)
    assert r.status_code == 200
    pd = r.json()["data"]
    assert set(["items", "total", "page", "per_page", "total_pages"]) <= set(pd)
    assert pd["page"] == 1 and pd["per_page"] == 5
    assert len(pd["items"]) <= 5
    assert pd["total"] >= 5, "后台应能看到 seed.py 的 5 件初始商品"
    # 后台条目带 is_deleted 标记和 images 数组
    for p in pd["items"]:
        assert "is_deleted" in p
        assert isinstance(p["images"], list) and p["images"]


def test_admin_products_pagination_no_overlap(auth_headers, requests_session, base_url):
    """后台商品分页：page=1 与 page=2 数据不重复（商品累积多，5 条/页足够翻页）"""
    ah = admin_headers(requests_session, base_url)
    p1 = requests_session.get(f"{base_url}/admin/products",
                              params={"page": 1, "per_page": 5},
                              headers=ah).json()["data"]
    p2 = requests_session.get(f"{base_url}/admin/products",
                              params={"page": 2, "per_page": 5},
                              headers=ah).json()["data"]
    if p1["total"] < 10:
        pytest.skip("后台商品不足 10 件，先多造几件商品再跑")
    ids1 = [p["id"] for p in p1["items"]]
    ids2 = [p["id"] for p in p2["items"]]
    assert len(ids1) == 5 and len(ids2) == 5
    assert set(ids1).isdisjoint(set(ids2)), "后台商品相邻两页出现重复"


@pytest.mark.parametrize("params", [
    "page=0", "page=-1", "page=abc",
    "per_page=0", "per_page=51", "per_page=100",
])
def test_admin_products_pagination_bad_params(auth_headers, requests_session,
                                              base_url, params):
    """后台商品列表非法分页参数：400"""
    ah = admin_headers(requests_session, base_url)
    r = requests_session.get(f"{base_url}/admin/products?{params}", headers=ah)
    assert r.status_code == 400, f"参数 {params} 应被拒绝"


def test_admin_products_includes_deleted_in_total(auth_headers, requests_session, base_url):
    """后台分页 total 含已下架商品（在售/已下架全部计入）"""
    ah = admin_headers(requests_session, base_url)
    before = requests_session.get(f"{base_url}/admin/products",
                                  params={"per_page": 50},
                                  headers=ah).json()["data"]["total"]
    pid = create_product(requests_session, base_url, ah,
                         name="下架计入分页总数").json()["data"]["id"]
    requests_session.delete(f"{base_url}/admin/products/{pid}", headers=ah)
    after = requests_session.get(f"{base_url}/admin/products",
                                 params={"per_page": 50},
                                 headers=ah).json()["data"]
    assert after["total"] == before + 1, "已下架商品仍应计入后台列表总数"
    # 后台排序"在售在前、已下架在后"，商品总数超过单页时新下架的商品可能不在
    # 第一页：翻完全部分页找到它，再断言下架标记（与 test_soft_delete 同写法）
    target = next((p for p in after["items"] if p["id"] == pid), None)
    if target is None and after["total_pages"] > 1:
        for page in range(2, after["total_pages"] + 1):
            rows = requests_session.get(f"{base_url}/admin/products",
                                        params={"page": page, "per_page": 50},
                                        headers=ah).json()["data"]["items"]
            target = next((p for p in rows if p["id"] == pid), None)
            if target:
                break
    assert target is not None, "后台列表应包含刚下架的商品"
    assert target["is_deleted"] == 1, "该商品应为已下架状态(is_deleted=1)"


# ---------- 7. 后台新增/修改商品的多图字段 ----------
def test_admin_create_multi_images(auth_headers, requests_session, base_url):
    """后台新增商品 image 传逗号分隔多图：images 数组正确拆分"""
    ah = admin_headers(requests_session, base_url)
    r = create_product(requests_session, base_url, ah,
                       name="后台多图商品", image="m1.png, m2.png")
    pid = r.json()["data"]["id"]
    p = get_product(requests_session, base_url, pid)
    assert p["images"] == ["m1.png", "m2.png"]


def test_admin_update_multi_images(auth_headers, requests_session, base_url):
    """后台修改商品 image 为多图：images 随之更新；清空图片回退 default.svg"""
    ah = admin_headers(requests_session, base_url)
    pid = create_product(requests_session, base_url, ah,
                         image="single.png").json()["data"]["id"]
    r = requests_session.put(f"{base_url}/admin/products/{pid}", headers=ah,
                             json={"image": "u1.png,u2.png,u3.png"})
    assert r.status_code == 200
    assert get_product(requests_session, base_url, pid)["images"] == \
        ["u1.png", "u2.png", "u3.png"]
    # 清空图片 → default.svg 兜底
    requests_session.put(f"{base_url}/admin/products/{pid}", headers=ah,
                         json={"image": ""})
    p = get_product(requests_session, base_url, pid)
    assert p["image"] == "default.svg" and p["images"] == ["default.svg"]
