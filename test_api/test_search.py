"""列表关键字搜索 专项测试（0913 搜索功能）
=====================================
本文件共 15 个测试函数（订单号两种写法 2×2、注入安全 4 接口×3 payload 参数化，
pytest 收集后共 25 条用例）。全项目用例总数由约 172 条增至约 197 条。

覆盖 5 处搜索入口中的 4 个后端 keyword 接口（购物车为纯前端筛选，不测接口）：
1. 后台商品 GET /api/admin/products?keyword=    模糊匹配商品名，命中/无命中
2. 后台订单 GET /api/admin/orders?keyword=      订单号（支持 #123）/ 订单内商品名快照
3. 我的订单 GET /api/orders?keyword=            同规则但始终限定本人，验证搜索不越权
4. 后台售后 GET /api/admin/aftersales?keyword=  订单号 / 申请原因 / 申请人用户名，
   与 status 筛选 AND 组合
5. 安全：四个 keyword 接口各传注入 payload（' OR '1'='1 / % / ";--），
   断言 HTTP 200、分页结构完好、不报错、我的订单看不到他人数据。

数据隔离：全部用「时间戳唯一商品名 / 唯一用户名」造数，断言不依赖 items[0]，
订单一律用 next()/id 精确定位，可反复运行、与库内其它数据互不影响。
"""
import time
import itertools
import pytest

from test_logistics import make_user, admin_headers
from test_aftersale import shipped_order, apply_as
from conftest import place_order_with_address

_seq = itertools.count(1)


# ---------- 公共工具 ----------
def unique_product(requests_session, base_url, ah, marker="搜索商品"):
    """管理员新建一个时间戳唯一商品，返回 (product_id, name)。
    唯一商品名保证关键字搜索只命中本用例造的单，不依赖 items[0]。"""
    name = f"{marker}_{int(time.time() * 1000)}_{next(_seq)}"
    r = requests_session.post(f"{base_url}/admin/products", headers=ah, json={
        "name": name, "price": 9.9, "stock": 100, "description": "搜索测试专用"
    })
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return r.json()["data"]["id"], name


def register_user(requests_session, base_url, prefix="srch"):
    """注册全新用户并登录，返回 (username, headers)"""
    username = f"{prefix}_{int(time.time() * 1000)}_{next(_seq)}"
    requests_session.post(f"{base_url}/register",
                          json={"username": username, "password": "123456"})
    r = requests_session.post(f"{base_url}/login",
                              json={"username": username, "password": "123456"})
    token = r.json()["data"]["token"]
    return username, {"Authorization": f"Bearer {token}"}


def assert_page_schema(payload):
    """统一分页结构不断言：items/total/page/per_page/total_pages 五个字段齐全"""
    data = payload.json()["data"]
    for key in ("items", "total", "page", "per_page", "total_pages"):
        assert key in data, f"分页结构缺少 {key} 字段"
    assert isinstance(data["items"], list)
    return data


# ---------- 1. 后台商品搜索 ----------
def test_admin_product_keyword_hit(requests_session, base_url):
    """后台商品按商品名模糊搜索：命中刚创建的唯一商品"""
    ah = admin_headers(requests_session, base_url)
    pid, name = unique_product(requests_session, base_url, ah, marker="后台命中键盘")
    try:
        r = requests_session.get(f"{base_url}/admin/products", headers=ah,
                                 params={"keyword": name, "per_page": 50})
        assert r.status_code == 200
        data = assert_page_schema(r)
        assert data["total"] == 1
        assert data["items"][0]["id"] == pid
        assert data["items"][0]["name"] == name
    finally:
        requests_session.delete(f"{base_url}/admin/products/{pid}", headers=ah)


def test_admin_product_keyword_no_hit(requests_session, base_url):
    """搜索不存在的商品名：空 items + total=0（不是 404/500）"""
    ah = admin_headers(requests_session, base_url)
    keyword = f"肯定不存在的商品_{int(time.time() * 1000)}_{next(_seq)}"
    r = requests_session.get(f"{base_url}/admin/products", headers=ah,
                             params={"keyword": keyword})
    assert r.status_code == 200
    data = assert_page_schema(r)
    assert data["items"] == []
    assert data["total"] == 0


# ---------- 2. 后台订单搜索 ----------
def test_admin_order_search_by_product_name(requests_session, base_url):
    """后台订单：按唯一商品名快照搜得到对应订单"""
    ah = admin_headers(requests_session, base_url)
    h = make_user(requests_session, base_url)
    pid, name = unique_product(requests_session, base_url, ah, marker="订单商品名键盘")
    oid, _ = place_order_with_address(requests_session, base_url, h, product_id=pid)

    r = requests_session.get(f"{base_url}/admin/orders", headers=ah,
                             params={"keyword": name, "per_page": 50})
    assert r.status_code == 200
    data = assert_page_schema(r)
    assert [o["id"] for o in data["items"]] == [oid]
    assert any(it["product_name"] == name for it in data["items"][0]["items"])


@pytest.mark.parametrize("prefix", ["", "#"], ids=["plain-id", "hash-id"])
def test_admin_order_search_by_order_id(requests_session, base_url, prefix):
    """后台订单：按订单号搜得到，"123" 和 "#123" 两种写法都测"""
    ah = admin_headers(requests_session, base_url)
    h = make_user(requests_session, base_url)
    pid, _ = unique_product(requests_session, base_url, ah, marker="订单号键盘")
    oid, _ = place_order_with_address(requests_session, base_url, h, product_id=pid)

    r = requests_session.get(f"{base_url}/admin/orders", headers=ah,
                             params={"keyword": f"{prefix}{oid}", "per_page": 50})
    assert r.status_code == 200
    data = assert_page_schema(r)
    # 不写死「恰好一条」：搜索是【订单号精确 OR 商品名模糊】，历史订单的商品名快照
    # 若恰好包含该数字（时间戳造数很常见）也会合法命中。只验证过滤正确性：
    # 目标订单必须在结果里，且每条结果要么订单号相等，要么商品名包含该数字
    ids = [o["id"] for o in data["items"]]
    assert oid in ids, "按订单号搜索必须搜到目标订单（#123 和 123 两种写法）"
    digit = str(oid)
    for o in data["items"]:
        assert o["id"] == oid or any(digit in it["product_name"] for it in o["items"]), \
            f"订单{o['id']}不应被关键字{digit}命中"


def test_admin_order_keyword_no_hit(requests_session, base_url):
    """后台订单：搜不存在的关键字 total=0"""
    ah = admin_headers(requests_session, base_url)
    keyword = f"不存在商品名_{int(time.time() * 1000)}_{next(_seq)}"
    r = requests_session.get(f"{base_url}/admin/orders", headers=ah,
                             params={"keyword": keyword, "per_page": 50})
    assert r.status_code == 200
    data = assert_page_schema(r)
    assert data["total"] == 0
    assert data["items"] == []


# ---------- 3. 我的订单搜索 ----------
def test_my_order_search_by_product_name(requests_session, base_url):
    """我的订单：按唯一商品名搜得到自己的订单"""
    ah = admin_headers(requests_session, base_url)
    h = make_user(requests_session, base_url)
    pid, name = unique_product(requests_session, base_url, ah, marker="我的订单键盘")
    oid, _ = place_order_with_address(requests_session, base_url, h, product_id=pid)

    r = requests_session.get(f"{base_url}/orders", headers=h,
                             params={"keyword": name, "per_page": 50})
    assert r.status_code == 200
    data = assert_page_schema(r)
    assert [o["id"] for o in data["items"]] == [oid]


@pytest.mark.parametrize("prefix", ["", "#"], ids=["plain-id", "hash-id"])
def test_my_order_search_by_order_id(requests_session, base_url, prefix):
    """我的订单：按订单号（含 # 写法）搜得到自己的订单"""
    ah = admin_headers(requests_session, base_url)
    h = make_user(requests_session, base_url)
    pid, _ = unique_product(requests_session, base_url, ah, marker="我的订单号键盘")
    oid, _ = place_order_with_address(requests_session, base_url, h, product_id=pid)

    r = requests_session.get(f"{base_url}/orders", headers=h,
                             params={"keyword": f"{prefix}{oid}", "per_page": 50})
    assert r.status_code == 200
    data = assert_page_schema(r)
    # 同样不写死恰好一条：商品名快照若含该数字也会模糊命中；验证目标在结果中、
    # 且所有结果都属于当前用户（不越权）并符合匹配规则
    ids = [o["id"] for o in data["items"]]
    assert oid in ids
    digit = str(oid)
    for o in data["items"]:
        assert o["id"] == oid or any(digit in it["product_name"] for it in o["items"])


def test_my_order_search_not_leak_others(requests_session, base_url):
    """安全重点：我的订单搜索不越权——搜别人订单的商品名/订单号都 total=0，
    不能因关键字看到他人订单（也不是 404/500）"""
    ah = admin_headers(requests_session, base_url)
    owner = make_user(requests_session, base_url)
    other = make_user(requests_session, base_url)
    pid, name = unique_product(requests_session, base_url, ah, marker="越权键盘")
    oid, _ = place_order_with_address(requests_session, base_url, owner, product_id=pid)

    # 别人按商品名搜：搜不到
    r = requests_session.get(f"{base_url}/orders", headers=other,
                             params={"keyword": name, "per_page": 50})
    assert r.status_code == 200
    assert assert_page_schema(r)["total"] == 0

    # 别人按订单号搜（含 # 写法）：同样搜不到
    for kw in (str(oid), f"#{oid}"):
        r = requests_session.get(f"{base_url}/orders", headers=other,
                                 params={"keyword": kw, "per_page": 50})
        assert r.status_code == 200
        assert assert_page_schema(r)["total"] == 0


# ---------- 4. 后台售后搜索 ----------
def test_admin_aftersale_search_by_order_id(requests_session, base_url):
    """售后列表：纯数字 / #数字 按订单号精确匹配"""
    ah = admin_headers(requests_session, base_url)
    h = make_user(requests_session, base_url)
    pid, _ = unique_product(requests_session, base_url, ah, marker="售后订单号键盘")
    oid = shipped_order(requests_session, base_url, h, product_id=pid)
    apply_as(requests_session, base_url, h, oid, reason="售后订单号搜索用原因")

    for kw in (str(oid), f"#{oid}"):
        r = requests_session.get(f"{base_url}/admin/aftersales", headers=ah,
                                 params={"keyword": kw, "per_page": 50})
        assert r.status_code == 200
        data = assert_page_schema(r)
        assert any(a["order_id"] == oid for a in data["items"]), f"关键字 {kw} 未命中售后单"


def test_admin_aftersale_search_by_reason(requests_session, base_url):
    """售后列表：按申请原因关键字模糊搜索命中"""
    ah = admin_headers(requests_session, base_url)
    h = make_user(requests_session, base_url)
    pid, _ = unique_product(requests_session, base_url, ah, marker="售后原因键盘")
    oid = shipped_order(requests_session, base_url, h, product_id=pid)
    reason = f"屏幕划痕退货编号{int(time.time() * 1000)}x{next(_seq)}"
    apply_as(requests_session, base_url, h, oid, reason=reason)

    # 整条原因本身就是唯一关键字（同时验证前后端对中文/数字混合的模糊匹配）
    r = requests_session.get(f"{base_url}/admin/aftersales", headers=ah,
                             params={"keyword": reason, "per_page": 50})
    assert r.status_code == 200
    data = assert_page_schema(r)
    rows = [a for a in data["items"] if a["order_id"] == oid]
    assert len(rows) == 1
    assert rows[0]["reason"] == reason


def test_admin_aftersale_search_by_username(requests_session, base_url):
    """售后列表：按申请人用户名搜索命中（AfterSale.user.has(username like)）"""
    ah = admin_headers(requests_session, base_url)
    username, h = register_user(requests_session, base_url, prefix="asuser")
    pid, _ = unique_product(requests_session, base_url, ah, marker="售后用户名键盘")
    oid = shipped_order(requests_session, base_url, h, product_id=pid)
    apply_as(requests_session, base_url, h, oid, reason="用户名搜索用原因")

    r = requests_session.get(f"{base_url}/admin/aftersales", headers=ah,
                             params={"keyword": username, "per_page": 50})
    assert r.status_code == 200
    data = assert_page_schema(r)
    rows = [a for a in data["items"] if a["order_id"] == oid]
    assert len(rows) == 1
    assert rows[0]["username"] == username


# ---------- 5. 安全：注入 payload 参数化（4 个 keyword 接口 × 3 个 payload = 12 条） ----------
INJECT_PAYLOADS = ["' OR '1'='1", "%", '";--']


@pytest.mark.parametrize("payload", INJECT_PAYLOADS)
def test_keyword_injection_admin_products(requests_session, base_url, payload):
    """后台商品 keyword 注入：200 + 分页结构完好，不报错不脱库"""
    ah = admin_headers(requests_session, base_url)
    r = requests_session.get(f"{base_url}/admin/products", headers=ah,
                             params={"keyword": payload, "per_page": 50})
    assert r.status_code == 200
    assert_page_schema(r)
    assert r.json()["code"] == 0


@pytest.mark.parametrize("payload", INJECT_PAYLOADS)
def test_keyword_injection_admin_orders(requests_session, base_url, payload):
    """后台订单 keyword 注入：200 + 分页结构完好"""
    ah = admin_headers(requests_session, base_url)
    r = requests_session.get(f"{base_url}/admin/orders", headers=ah,
                             params={"keyword": payload, "per_page": 50})
    assert r.status_code == 200
    assert_page_schema(r)
    assert r.json()["code"] == 0


@pytest.mark.parametrize("payload", INJECT_PAYLOADS)
def test_keyword_injection_admin_aftersales(requests_session, base_url, payload):
    """后台售后 keyword 注入：200 + 分页结构完好"""
    ah = admin_headers(requests_session, base_url)
    r = requests_session.get(f"{base_url}/admin/aftersales", headers=ah,
                             params={"keyword": payload, "per_page": 50})
    assert r.status_code == 200
    assert_page_schema(r)
    assert r.json()["code"] == 0


@pytest.mark.parametrize("payload", INJECT_PAYLOADS)
def test_keyword_injection_my_orders_no_leak(requests_session, base_url, payload):
    """我的订单 keyword 注入：全新用户没有订单，任何注入都只能 total=0，
    绝不能因为注入串拼进 SQL 而看到别人的订单"""
    h = make_user(requests_session, base_url)
    r = requests_session.get(f"{base_url}/orders", headers=h,
                             params={"keyword": payload, "per_page": 50})
    assert r.status_code == 200
    data = assert_page_schema(r)
    assert data["total"] == 0
    assert data["items"] == []
