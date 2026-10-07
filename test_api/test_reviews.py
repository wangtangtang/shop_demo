"""商品评价（订单级）专项测试（0917 交易闭环补全）
=====================================
本文件共 28 个测试函数（评分/非法参数参数化展开后共 37 条用例）。
全项目用例总数由 239 条增至 283 条（本文件 +37，test_payment.py 支付单关闭 +7）。

评价模型：一笔已完成订单(completed)对应一条 Review（DB unique + 接口双校验），
评价关联整笔订单；商品维度的评价通过 OrderItem 反查聚合。

覆盖点（顾客端）：
- 正常评价（rating 参数化 1/3/5）；创建返回详情含购买商品名快照
- 状态门槛：pending/paid/shipped 订单评价 → 400
- 重复评价 → 400；他人订单 → 404；未登录 → 401
- rating 非法参数化：0 / 6 / -1 / 2.5 / "5" / None / true(bool) / 缺失 → 全 400
- content：空串 / 纯空格 / 缺失 → 400；501 字 → 400；500 字 → 成功
- 查自己的评价（GET）；无评价 data=null
- 商品公开评价列表（游客可看、统一分页结构、购买商品名聚合）
- 同订单买 2 件同一商品只出现 1 条评价（重点：去重）
- 只返回含该商品订单的评价（别的商品评价不串入）
- 汇总：avg/count/good_rate/rating_dist 正确（5星+3星 → avg 4.0/count 2/good_rate 50）
- 软删除评价不进公开列表且汇总排除；商品下架后历史评价仍可追溯；商品物理不存在 404
- 公开列表用户名脱敏（首字 + **），不泄露全称

覆盖点（管理端）：
- 评价管理列表（含 is_deleted 标记）、搜索 keyword、deleted 筛选；普通用户 403
- 管理员软删除幂等；删除后公开列表与汇总更新
- 越权：A 看不到 / 不能评价 B 的订单评价
"""
import time

import pytest

from test_logistics import make_user, admin_headers, paid_order, ship_body
from conftest import create_address


# ---------- 链路 helper ----------

def completed_order(requests_session, base_url, headers, product_id=1, qty=1):
    """建地址 → 下单 → 支付 → 管理员发货 → 用户确认收货，
    返回 completed（已完成、可评价）状态的 order_id。"""
    ah = admin_headers(requests_session, base_url)
    aid = create_address(requests_session, base_url, headers)
    requests_session.post(f"{base_url}/cart", headers=headers,
                          json={"product_id": product_id, "quantity": qty})
    r = requests_session.post(f"{base_url}/orders", headers=headers,
                              json={"address_id": aid})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    oid = r.json()["data"]["order_id"]
    requests_session.post(f"{base_url}/orders/{oid}/pay", headers=headers,
                          json={"channel": "wechat"})
    requests_session.post(f"{base_url}/pay/mock-notify", json={"order_id": oid})
    requests_session.post(f"{base_url}/admin/orders/{oid}/ship", headers=ah,
                          json=ship_body())
    r = requests_session.post(f"{base_url}/orders/{oid}/action", headers=headers,
                              json={"action": "confirm"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return oid


def pending_order(requests_session, base_url, headers, product_id=2):
    """只下单不支付，返回 pending order_id（状态门槛用例用）。"""
    aid = create_address(requests_session, base_url, headers)
    requests_session.post(f"{base_url}/cart", headers=headers,
                          json={"product_id": product_id, "quantity": 1})
    r = requests_session.post(f"{base_url}/orders", headers=headers,
                              json={"address_id": aid})
    return r.json()["data"]["order_id"]


def post_review(requests_session, base_url, headers, oid, rating=5,
                content="商品质量很好，值得购买"):
    """提交评价，返回 response（用例自己断言状态码）。"""
    return requests_session.post(f"{base_url}/orders/{oid}/reviews",
                                 headers=headers,
                                 json={"rating": rating, "content": content})


def make_product(requests_session, base_url, name):
    """管理员新建专用商品，返回 product_id。
    汇总/空态类用例用专用商品，彻底隔离种子商品上的历史评价数据。"""
    ah = admin_headers(requests_session, base_url)
    r = requests_session.post(f"{base_url}/admin/products", headers=ah,
                              json={"name": name, "price": 10.0, "stock": 50,
                                    "description": "评价测试专用商品"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return r.json()["data"]["id"]


# ---------- 顾客端：创建评价 ----------

@pytest.mark.parametrize("rating", [1, 3, 5])
def test_create_review_ok(requests_session, base_url, rating):
    """已完成订单评价成功：rating 参数化 1/3/5，返回详情含评分/内容/购买商品名"""
    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    r = post_review(requests_session, base_url, h, oid, rating=rating,
                    content=f"{rating}星评价，测试内容")
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    data = r.json()["data"]
    assert data["order_id"] == oid
    assert data["rating"] == rating
    assert data["content"] == f"{rating}星评价，测试内容"
    assert data["created_at"]
    # 订单级评价：聚合该订单全部 OrderItem 的商品名快照
    assert "机械键盘" in data["product_names"]


def test_review_pending_order_400(requests_session, base_url):
    """pending（待支付）订单不能评价 → 400"""
    h = make_user(requests_session, base_url)
    oid = pending_order(requests_session, base_url, h)
    r = post_review(requests_session, base_url, h, oid)
    assert r.status_code == 400
    assert "只有已完成的订单可以评价" in r.json()["msg"]


def test_review_paid_order_400(requests_session, base_url):
    """paid（已付款未发货）订单不能评价 → 400"""
    h = make_user(requests_session, base_url)
    oid, _ = paid_order(requests_session, base_url, h, product_id=3)
    r = post_review(requests_session, base_url, h, oid)
    assert r.status_code == 400
    assert "只有已完成的订单可以评价" in r.json()["msg"]


def test_review_shipped_order_400(requests_session, base_url):
    """shipped（已发货未收货）订单不能评价 → 400"""
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid, _ = paid_order(requests_session, base_url, h, product_id=4)
    requests_session.post(f"{base_url}/admin/orders/{oid}/ship", headers=ah,
                          json=ship_body())
    r = post_review(requests_session, base_url, h, oid)
    assert r.status_code == 400


def test_review_duplicate_400(requests_session, base_url):
    """同一订单重复评价 → 400（DB unique + 接口双校验）"""
    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    r1 = post_review(requests_session, base_url, h, oid)
    assert r1.json()["code"] == 0
    r2 = post_review(requests_session, base_url, h, oid)
    assert r2.status_code == 400
    assert "不能重复评价" in r2.json()["msg"]


def test_review_others_order_404(requests_session, base_url):
    """不能给别人的订单评价（越权 → 404）"""
    h = make_user(requests_session, base_url)
    other = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    r = post_review(requests_session, base_url, other, oid)
    assert r.status_code == 404


def test_review_requires_login(requests_session, base_url):
    """未登录提交评价 → 401"""
    r = requests_session.post(f"{base_url}/orders/1/reviews",
                              json={"rating": 5, "content": "未登录评价"})
    assert r.status_code == 401


@pytest.mark.parametrize("bad_rating", [0, 6, -1, 2.5, "5", None, True])
def test_review_invalid_rating_400(requests_session, base_url, bad_rating):
    """评分非法值参数化：0 / 6 / -1 / 2.5 / "5"(字符串) / None / true(bool) 全 400。
    bool 是 int 的子类，后端必须显式拒绝。"""
    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    r = requests_session.post(f"{base_url}/orders/{oid}/reviews", headers=h,
                              json={"rating": bad_rating, "content": "非法评分测试"})
    assert r.status_code == 400
    assert "评分必须是1-5的整数" in r.json()["msg"]


def test_review_missing_rating_400(requests_session, base_url):
    """缺失 rating 字段 → 400"""
    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    r = requests_session.post(f"{base_url}/orders/{oid}/reviews", headers=h,
                              json={"content": "没给评分"})
    assert r.status_code == 400
    assert "评分必须是1-5的整数" in r.json()["msg"]


@pytest.mark.parametrize("bad_content,label", [
    ("", "空串"),
    ("   \n\t  ", "纯空白"),
])
def test_review_blank_content_400(requests_session, base_url, bad_content, label):
    """空串 / 纯空格内容 → 400（strip 后非空校验）"""
    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    r = post_review(requests_session, base_url, h, oid, content=bad_content)
    assert r.status_code == 400, label
    assert "评价内容" in r.json()["msg"]


def test_review_missing_content_400(requests_session, base_url):
    """缺失 content 字段 → 400"""
    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    r = requests_session.post(f"{base_url}/orders/{oid}/reviews", headers=h,
                              json={"rating": 5})
    assert r.status_code == 400


def test_review_content_too_long_400(requests_session, base_url):
    """501 字评价 → 400（上限 500 字符，按 Python 字符数）"""
    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    r = post_review(requests_session, base_url, h, oid, content="好" * 501)
    assert r.status_code == 400
    assert "500" in r.json()["msg"]


def test_review_content_500_chars_ok(requests_session, base_url):
    """正好 500 字评价 → 成功（边界）"""
    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    content = "棒" * 500
    r = post_review(requests_session, base_url, h, oid, content=content)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    assert len(r.json()["data"]["content"]) == 500


# ---------- 顾客端：查自己的评价 ----------

def test_get_my_review_ok(requests_session, base_url):
    """GET 订单评价：属主可看，返回详情"""
    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    post_review(requests_session, base_url, h, oid, content="我的评价内容")
    r = requests_session.get(f"{base_url}/orders/{oid}/reviews", headers=h)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    assert r.json()["data"]["content"] == "我的评价内容"


def test_get_my_review_empty_null(requests_session, base_url):
    """订单无评价时 GET：data=null（不报错）"""
    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    r = requests_session.get(f"{base_url}/orders/{oid}/reviews", headers=h)
    assert r.status_code == 200 and r.json()["code"] == 0
    assert r.json()["data"] is None


def test_get_review_others_order_404(requests_session, base_url):
    """越权查看他人订单评价 → 404"""
    h = make_user(requests_session, base_url)
    other = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    r = requests_session.get(f"{base_url}/orders/{oid}/reviews", headers=other)
    assert r.status_code == 404


def test_order_json_reviewed_flag(requests_session, base_url):
    """订单详情/列表带 reviewed 标记：评价前 false，评价后 true"""
    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    r = requests_session.get(f"{base_url}/orders/{oid}", headers=h)
    assert r.json()["data"]["reviewed"] is False
    post_review(requests_session, base_url, h, oid)
    r = requests_session.get(f"{base_url}/orders/{oid}", headers=h)
    assert r.json()["data"]["reviewed"] is True


# ---------- 商品公开评价列表（游客可看 + 分页 + 去重 + 汇总） ----------

def test_product_reviews_public_and_pagination(requests_session, base_url):
    """游客（不带 token）可看商品评价；返回统一分页结构
    {items,total,page,per_page,total_pages} + summary。"""
    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    post_review(requests_session, base_url, h, oid, rating=5, content="公开列表可见")

    r = requests_session.get(f"{base_url}/products/1/reviews?page=1&per_page=10")
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    data = r.json()["data"]
    for key in ("items", "total", "page", "per_page", "total_pages"):
        assert key in data, key
    assert data["page"] == 1
    assert data["per_page"] == 10
    assert "summary" in data


def test_product_reviews_only_this_product(requests_session, base_url):
    """只返回含该商品订单的评价：给商品1、商品2各造一条评价，
    商品1列表只看到商品1那条，不串入商品2。"""
    h1 = make_user(requests_session, base_url)
    h2 = make_user(requests_session, base_url)
    oid1 = completed_order(requests_session, base_url, h1, product_id=1)
    post_review(requests_session, base_url, h1, oid1, content="键盘的评价")
    oid2 = completed_order(requests_session, base_url, h2, product_id=2)
    post_review(requests_session, base_url, h2, oid2, content="鼠标的评价")

    r = requests_session.get(f"{base_url}/products/1/reviews?per_page=50")
    items = r.json()["data"]["items"]
    contents = [it["content"] for it in items]
    assert "键盘的评价" in contents
    assert "鼠标的评价" not in contents


def test_product_reviews_dedup_two_same_items(requests_session, base_url):
    """重点去重用例：同一订单买 2 件同一商品，商品评价列表中该订单评价只出现 1 次。"""
    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1, qty=2)
    post_review(requests_session, base_url, h, oid, content="买了两个键盘，只评价一次")

    r = requests_session.get(f"{base_url}/products/1/reviews?per_page=50")
    items = r.json()["data"]["items"]
    hits = [it for it in items if it["order_id"] == oid]
    assert len(hits) == 1, f"同订单评价重复出现：{hits}"


def test_product_review_username_masked(requests_session, base_url):
    """公开列表不泄露买家用户名全称：脱敏为首字 + **（如 张**）"""
    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    post_review(requests_session, base_url, h, oid)
    r = requests_session.get(f"{base_url}/products/1/reviews?per_page=50")
    items = r.json()["data"]["items"]
    mine = [it for it in items if it["order_id"] == oid][0]
    # make_user 生成的用户名是 log_ 开头的毫秒串，脱敏后应为 log... 截断成首字 + **
    assert mine["username"].endswith("**")
    assert "log_" not in mine["username"]  # 完整用户名不可见


def test_product_review_summary_stats(requests_session, base_url):
    """汇总正确性：新建专用商品，造 5 星 1 条、3 星 1 条（独立用户、独立订单）
    → avg=4.0、count=2、good_rate=50（4-5 星占比）、rating_dist 分布正确。"""
    product_id = make_product(requests_session, base_url,
                              f"汇总统计商品{int(time.time()*1000)}")
    for rating in (5, 3):
        h = make_user(requests_session, base_url)
        oid = completed_order(requests_session, base_url, h, product_id=product_id)
        post_review(requests_session, base_url, h, oid, rating=rating,
                    content=f"{rating}星")

    r = requests_session.get(f"{base_url}/products/{product_id}/reviews?per_page=50")
    data = r.json()["data"]
    s = data["summary"]
    # 专用商品，库里只有这两条评价
    assert s["count"] == 2
    assert s["avg_rating"] == 4.0
    assert s["good_rate"] == 50
    assert s["rating_dist"]["5"] == 1
    assert s["rating_dist"]["3"] == 1
    assert s["rating_dist"]["1"] == 0
    assert len(data["items"]) == 2


def test_product_review_empty(requests_session, base_url):
    """无评价商品：items 空、total 0、summary 全空形态（avg null、dist 全 0）。
    用新建的专用商品，保证没被其他用例造过评价。"""
    product_id = make_product(requests_session, base_url,
                              f"零评价商品{int(time.time()*1000)}")
    r = requests_session.get(f"{base_url}/products/{product_id}/reviews")
    data = r.json()["data"]
    assert data["items"] == []
    assert data["total"] == 0
    assert data["summary"]["count"] == 0
    assert data["summary"]["avg_rating"] is None
    assert data["summary"]["good_rate"] == 0


def test_product_reviews_product_not_found_404(requests_session, base_url):
    """商品物理不存在 → 404"""
    r = requests_session.get(f"{base_url}/products/999999/reviews")
    assert r.status_code == 404


def test_product_reviews_after_product_offline(requests_session, base_url):
    """商品下架（软删除）后历史评价仍允许游客追溯（软删除哲学），接口 200。
    用管理员新建的专用商品，避免把种子商品 5 下架影响其他用例。"""
    ah = admin_headers(requests_session, base_url)
    r = requests_session.post(f"{base_url}/admin/products", headers=ah,
                              json={"name": f"待下架商品{int(time.time())}",
                                    "price": 19.9, "stock": 10,
                                    "description": "下架可追溯用"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    pid = r.json()["data"]["id"]

    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=pid)
    post_review(requests_session, base_url, h, oid, content="下架前买的")

    r = requests_session.delete(f"{base_url}/admin/products/{pid}", headers=ah)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text

    # 游客（不带 token）仍可翻历史评价
    r = requests_session.get(f"{base_url}/products/{pid}/reviews")
    assert r.status_code == 200 and r.json()["code"] == 0
    data = r.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["content"] == "下架前买的"


# ---------- 管理端：评价管理 ----------

def test_admin_review_list_ok(requests_session, base_url):
    """管理员可看全部评价，条目带订单号/用户名/评分/内容/is_deleted 标记；
    普通用户 → 403。"""
    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    post_review(requests_session, base_url, h, oid, content="后台可见的评价")

    ah = admin_headers(requests_session, base_url)
    r = requests_session.get(f"{base_url}/admin/reviews?page=1&per_page=10",
                             headers=ah)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    items = r.json()["data"]["items"]
    mine = [it for it in items if it["order_id"] == oid][0]
    assert mine["content"] == "后台可见的评价"
    assert mine["is_deleted"] == 0
    assert "order_no" in mine and "username" in mine
    # 后台列表显示完整用户名，不脱敏
    assert "log_" in mine["username"]

    # 普通用户访问后台评价列表 → 403
    r = requests_session.get(f"{base_url}/admin/reviews", headers=h)
    assert r.status_code == 403


def test_admin_review_keyword_and_status_filter(requests_session, base_url):
    """keyword 搜评价内容能命中；status=deleted 只返回已删除评价"""
    ah = admin_headers(requests_session, base_url)
    h = make_user(requests_session, base_url)
    oid = completed_order(requests_session, base_url, h, product_id=1)
    token = f"独特暗号XYZ{int(time.time())}"
    post_review(requests_session, base_url, h, oid, content=token)

    r = requests_session.get(f"{base_url}/admin/reviews", headers=ah,
                             params={"keyword": token})
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    assert len(items) == 1 and items[0]["content"] == token

    # 删除后：all 列表带 is_deleted=1；deleted 筛选能看到
    rid = items[0]["id"]
    requests_session.post(f"{base_url}/admin/reviews/{rid}/delete", headers=ah)
    r = requests_session.get(f"{base_url}/admin/reviews", headers=ah,
                             params={"keyword": token, "status": "deleted"})
    items = r.json()["data"]["items"]
    assert len(items) == 1 and items[0]["is_deleted"] == 1


def test_admin_soft_delete_review_and_public_hidden(requests_session, base_url):
    """管理员软删除：公开列表立即消失、汇总同步排除；重复删除幂等成功；
    普通用户调删除接口 → 403。"""
    ah = admin_headers(requests_session, base_url)
    h = make_user(requests_session, base_url)
    other = make_user(requests_session, base_url)
    product_id = make_product(requests_session, base_url,
                              f"删评专用商品{int(time.time()*1000)}")
    oid = completed_order(requests_session, base_url, h, product_id=product_id)
    post_review(requests_session, base_url, h, oid, rating=4, content="待删除评价")

    rid = requests_session.get(f"{base_url}/orders/{oid}/reviews", headers=h).json()["data"]["id"]

    # 普通用户不能删
    r = requests_session.post(f"{base_url}/admin/reviews/{rid}/delete", headers=other)
    assert r.status_code == 403

    # 管理员软删除
    r = requests_session.post(f"{base_url}/admin/reviews/{rid}/delete", headers=ah)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    # 幂等：再删一次仍成功
    r = requests_session.post(f"{base_url}/admin/reviews/{rid}/delete", headers=ah)
    assert r.status_code == 200 and r.json()["code"] == 0

    # 公开列表消失 + 汇总排除（该商品只剩这一条评价，删后应为空）
    r = requests_session.get(f"{base_url}/products/{product_id}/reviews?per_page=50")
    data = r.json()["data"]
    assert all(it["id"] != rid for it in data["items"])
    assert data["summary"]["count"] == 0

    # 顾客自己查订单评价也看不到（is_deleted 过滤）
    r = requests_session.get(f"{base_url}/orders/{oid}/reviews", headers=h)
    assert r.json()["data"] is None
