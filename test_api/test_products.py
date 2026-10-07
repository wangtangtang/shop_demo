"""商品接口测试（含分页 + images 多图字段）

分页接口返回统一结构 data: {items, total, page, per_page, total_pages}，
参数校验规则与订单分页一致：page/per_page 必须是正整数，per_page 上限 50。
images 字段：image 列存英文逗号分隔的多个文件名，后端 split 成数组；
为空时返回 ["default.svg"]；原 image 单字段保留（兼容）。
"""
import time
import pytest


def admin_headers(requests_session, base_url):
    resp = requests_session.post(f"{base_url}/login",
                                 json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, "管理员登录失败，检查 seed.py"
    token = resp.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


def create_product(requests_session, base_url, ah, **fields):
    """后台新增商品，返回响应；默认字段可被 fields 覆盖"""
    body = {"name": f"分页商品{int(time.time() * 1000)}",
            "price": 10.0, "stock": 5}
    body.update(fields)
    return requests_session.post(f"{base_url}/admin/products",
                                 headers=ah, json=body)


# ---------- 1. 商品列表（分页结构） ----------
def test_list_products(register_user, requests_session, base_url):
    resp = requests_session.get(f"{base_url}/products")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    pd = data["data"]
    # 分页结构字段齐全
    assert set(["items", "total", "page", "per_page", "total_pages"]) <= set(pd)
    assert pd["page"] == 1
    assert pd["per_page"] == 12          # 默认每页 12 件
    assert pd["total"] == len(_all_product_ids(requests_session, base_url))
    assert pd["total_pages"] == (pd["total"] + 11) // 12
    # 初始 5 件商品必须存在（在全部页里找）；测试/手动新增的商品会累积，不写死总数
    ids = set(_all_product_ids(requests_session, base_url))
    assert {1, 2, 3, 4, 5} <= ids, "seed.py 初始的 5 件商品缺失"
    for p in pd["items"]:
        assert p["name"] and p["price"] > 0


def _all_product_ids(requests_session, base_url):
    """翻完所有分页，收集全部商品 id（用例不假设单页能装下全部商品）"""
    ids, page = [], 1
    while True:
        pd = requests_session.get(
            f"{base_url}/products", params={"page": page, "per_page": 50}
        ).json()["data"]
        ids.extend(p["id"] for p in pd["items"])
        if page >= pd["total_pages"]:
            break
        page += 1
    return ids


def test_keyword_search(register_user, requests_session, base_url):
    resp = requests_session.get(f"{base_url}/products", params={"keyword": "键盘"})
    data = resp.json()
    assert data["code"] == 0
    pd = data["data"]
    # 不写死 total（测试数据会积累，库里可能有多个键盘商品）；
    # 搜索测试验证的是过滤正确性：total>=1，且本页每个商品名都包含关键字
    assert pd["total"] >= 1
    assert len(pd["items"]) >= 1
    assert all("键盘" in p["name"] for p in pd["items"])


def test_product_detail(register_user, requests_session, base_url):
    resp = requests_session.get(f"{base_url}/products/1")
    data = resp.json()
    assert data["code"] == 0
    assert data["data"]["id"] == 1


def test_product_not_found(register_user, requests_session, base_url):
    resp = requests_session.get(f"{base_url}/products/9999")
    assert resp.status_code == 404
    assert resp.json()["code"] == 404


# ---------- 2. 商品分页 ----------
def test_products_pagination_structure(auth_headers, requests_session, base_url):
    """默认请求即返回分页结构，字段齐全；per_page 生效"""
    r = requests_session.get(f"{base_url}/products",
                             params={"page": 1, "per_page": 5})
    assert r.status_code == 200
    pd = r.json()["data"]
    assert pd["page"] == 1
    assert pd["per_page"] == 5
    assert len(pd["items"]) == 5
    assert pd["total"] >= 5
    assert pd["total_pages"] == (pd["total"] + 4) // 5


def test_products_pagination_no_overlap(auth_headers, requests_session, base_url):
    """page=1 与 page=2 的商品不重复（各页数据不交叉）"""
    p1 = requests_session.get(f"{base_url}/products",
                              params={"page": 1, "per_page": 5}).json()["data"]
    p2 = requests_session.get(f"{base_url}/products",
                              params={"page": 2, "per_page": 5}).json()["data"]
    ids1 = [p["id"] for p in p1["items"]]
    ids2 = [p["id"] for p in p2["items"]]
    assert len(ids1) == 5 and len(ids2) == 5, "测试数据不足 10 件时本用例前提不成立"
    assert set(ids1).isdisjoint(set(ids2)), "相邻两页出现了重复商品"


@pytest.mark.parametrize("params", [
    "page=0",        # 页码从 1 开始
    "page=-1",       # 负数
    "page=abc",      # 非数字
    "per_page=0",    # 每页 0 条没意义
    "per_page=51",   # 超过上限 50（防止一次拉全表）
    "per_page=100",
])
def test_products_pagination_bad_params(auth_headers, requests_session,
                                        base_url, params):
    """非法分页参数必须 400，不能 500 也不能静默忽略（与订单分页校验一致）"""
    r = requests_session.get(f"{base_url}/products?{params}")
    assert r.status_code == 400, f"参数 {params} 应被拒绝"


def test_products_page_out_of_range(auth_headers, requests_session, base_url):
    """页码越界（999 页）：不报错，返回空 items，total_pages 正常"""
    r = requests_session.get(f"{base_url}/products",
                             params={"page": 999, "per_page": 12})
    assert r.status_code == 200
    pd = r.json()["data"]
    assert pd["items"] == []
    assert pd["total"] >= 5


# ---------- 3. 商品 images 多图字段 ----------
def test_product_images_field_is_array(auth_headers, requests_session, base_url):
    """普通商品：列表和详情都返回 images 数组，且包含其图片文件名"""
    pd = requests_session.get(f"{base_url}/products",
                              params={"per_page": 5}).json()["data"]
    for p in pd["items"]:
        assert isinstance(p["images"], list), "images 必须是数组"
        assert p["images"], "images 不能为空（至少有 default.svg 兜底）"
        assert p["image"] in p["images"], "image 单字段应是 images 的第一张/成员"
    # 详情接口同样返回 images
    pid = pd["items"][0]["id"]
    detail = requests_session.get(f"{base_url}/products/{pid}").json()["data"]
    assert isinstance(detail["images"], list) and detail["images"]


def test_product_empty_image_uses_default(auth_headers, requests_session, base_url):
    """image 为空的商品：image 兜底 default.svg，images 为 ["default.svg"]"""
    ah = admin_headers(requests_session, base_url)
    r = create_product(requests_session, base_url, ah,
                       name="无图商品images", image="")
    pid = r.json()["data"]["id"]
    p = requests_session.get(f"{base_url}/products/{pid}").json()["data"]
    assert p["image"] == "default.svg"
    assert p["images"] == ["default.svg"]


def test_product_multi_images_split(auth_headers, requests_session, base_url):
    """逗号分隔多图：image 传 "a.png,b.png"，images 正确 split 成多张
    （带空格也能去空格；列表接口和详情接口都要对）"""
    ah = admin_headers(requests_session, base_url)
    r = create_product(requests_session, base_url, ah,
                       name="多图轮播商品", image=" a.png , b.png ,, c.png ")
    assert r.status_code == 200
    pid = r.json()["data"]["id"]

    detail = requests_session.get(f"{base_url}/products/{pid}").json()["data"]
    assert detail["images"] == ["a.png", "b.png", "c.png"], \
        "多图应按英文逗号 split、去空格去空串"

    # 列表接口（分页 items）里也能查到该商品的 images
    found = next((p for p in _all_items(requests_session, base_url)
                  if p["id"] == pid), None)
    assert found is not None
    assert found["images"] == ["a.png", "b.png", "c.png"]


def _all_items(requests_session, base_url):
    """翻完商品列表所有分页，返回全部 items"""
    items, page = [], 1
    while True:
        pd = requests_session.get(
            f"{base_url}/products", params={"page": page, "per_page": 50}
        ).json()["data"]
        items.extend(pd["items"])
        if page >= pd["total_pages"]:
            break
        page += 1
    return items
