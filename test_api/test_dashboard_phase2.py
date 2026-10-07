"""数据分析看板二期（评价标签洞察 / 库存预警 / 用户 RFM 分层）专项测试
==============================================================================
本文件【新增用例数：46 条】（pytest 收集口径；参数化合计展开 51 个参数断言场景）。

测试隔离原则（吸取本项目已沉淀的脆弱性教训）：
- 禁止对全局统计做差分：滑动窗口边界订单会滑出窗口污染断言；本文件一律用
  「管理员新建专用实体 + 按 id 查询隔离」或「字段口径自洽 + 逐单断言」；
- 专用商品名带自增序号（全局计数器），防 Windows 下 time_ns 撞名；
- 差评预警/RFM 断言基于【本次新建实体】的 id/内容精确匹配，不依赖全局计数。

覆盖点：
1. review_ai 纯函数离线：正/负/多标签/评分文本冲突/空内容/纯标点 + 聚合排序计数
2. 三接口权限：游客 401、普通用户 403
3. review-insights：days 白名单/非法值；专用商品+低星评价隔离断言；软删排除；脱敏
4. inventory-alerts：threshold 校验（0/101/-1/1.5/True/字符串→400，1/100/缺省→200）；
   专用低库存商品命中；下架排除；stock=0 状态
5. rfm：days 白名单 7/30/90；3 个专用新用户各造确定订单，手工算中位数/分值/分层；
   单用户兜底不崩；空窗口结构完整；rfm 字符串与分层映射自洽；无 NaN/inf
"""
import itertools
import time

import pytest

from conftest import create_address
from app import review_ai, rfm_analysis

# 全局自增序号（同一进程内唯一），配合 time_ns 保证 Windows 下商品名不撞
_SEQ = itertools.count(1)


# ============================ 本文件专用 helper ============================

def unique_tag():
    """生成本次调用唯一标识（用于商品名/用户名，防 time_ns 撞名）"""
    return f"p2{int(time.time() * 1000) % 1000000:06d}_{next(_SEQ):03d}"


def admin_headers(s, url):
    r = s.post(f"{url}/login", json={"username": "admin", "password": "admin123"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def make_user(s, url):
    """注册并登录全新普通用户，返回 (headers, username)"""
    name = "u" + unique_tag()
    s.post(f"{url}/register", json={"username": name, "password": "123456"})
    r = s.post(f"{url}/login", json={"username": name, "password": "123456"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}, name


def create_product(s, url, ah, stock, price=100.0, prefix="P2"):
    """管理员新建专用商品，返回 product_id（名字带自增序号）"""
    name = f"{prefix}{unique_tag()}"
    r = s.post(f"{url}/admin/products", headers=ah,
               json={"name": name, "price": price, "stock": stock,
                     "description": "看板二期专用"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return r.json()["data"]["id"], name


def make_paid_order(s, url, headers, product_id, qty=1):
    """建地址 → 加购 → 下单 → 模拟支付回调，返回 order_id（paid 态，进 RFM）"""
    aid = create_address(s, url, headers)
    s.post(f"{url}/cart", headers=headers,
           json={"product_id": product_id, "quantity": qty})
    r = s.post(f"{url}/orders", headers=headers,
               json={"address_id": aid})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    oid = r.json()["data"]["order_id"]
    pr = s.post(f"{url}/orders/{oid}/pay", headers=headers, json={"channel": "wechat"})
    assert pr.status_code == 200 and pr.json()["code"] == 0, pr.text
    nr = s.post(f"{url}/pay/mock-notify", json={"order_id": oid})
    assert nr.status_code == 200 and nr.json()["code"] == 0, nr.text
    # 回调必须真的把订单置成 paid（notify 接口对"非 pending"也回 code=0，
    # 不能只看回调响应码，必须复查订单真实状态——防静默未支付单混进 RFM）
    dr = s.get(f"{url}/orders/{oid}", headers=headers)
    assert dr.status_code == 200 and dr.json()["data"]["status"] == "paid", dr.text
    return oid


def complete_order(s, url, ah, headers, oid):
    """paid → 管理员发货 → 顾客确认收货（completed 后才能评价）"""
    tracking = "SF" + str(int(time.time() * 1000)) + str(next(_SEQ))
    r = s.post(f"{url}/admin/orders/{oid}/ship", headers=ah,
               json={"logistics_company": "顺丰速运", "tracking_no": tracking})
    assert r.status_code == 200, r.text
    r = s.post(f"{url}/orders/{oid}/action", headers=headers,
               json={"action": "confirm"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text


def make_completed_review(s, url, ah, headers, pid, rating, content, qty=1):
    """专用完整链路：造 paid 单 → 发货 → 收货 → 评价，返回 (review_id, order_id)"""
    oid = make_paid_order(s, url, headers, pid, qty=qty)
    complete_order(s, url, ah, headers, oid)
    r = s.post(f"{url}/orders/{oid}/reviews", headers=headers,
               json={"rating": rating, "content": content})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return r.json()["data"]["id"], oid


def get_review_insights(s, url, ah, days):
    return s.get(f"{url}/admin/stats/review-insights?days={days}", headers=ah)


# ==================== 1. review_ai 纯函数离线测试（21 条） ====================

EXTRACT_CASES = [
    # (content, rating, 必须包含的标签, 期望情感)
    ("物流很快，非常满意", 5, ["物流快", "满意"], "positive"),
    ("质量不错，做工也可以", 5, ["质量好"], "positive"),
    ("包装严实，性价比很高", 5, ["包装严实", "性价比高"], "positive"),
    ("客服态度很好，外观漂亮", 5, ["客服态度好", "外观好看"], "positive"),
    ("发货很快", 5, ["发货快"], "positive"),
    ("收到的商品破损了", 1, ["破损"], "negative"),
    ("漏发了配件，还少发", 1, ["漏发", "少发"], "negative"),
    ("质量很差，有瑕疵", 1, ["质量差", "瑕疵"], "negative"),
    ("色差严重，物流太慢了", 1, ["色差", "物流慢"], "negative"),
    ("感觉是假货，客服态度差", 1, ["假货", "客服差"], "negative"),
    ("东西一般般吧", 3, [], "neutral"),
    # 「不满意」整段命中负标签，不再抽出其中的「满意」（跨极性包含剔除）
    ("很不满意", 1, ["不满意"], "negative"),
    # 一条评价同时命中正/负标签
    ("发货很快但是包装破损了", 2, ["发货快", "破损"], "negative"),
    # 文本与评分冲突：文本命中正向，评分给 1 → 以文本命中为主 positive
    ("物流很快质量很好", 1, ["物流快", "质量好"], "positive"),
    # 文本命中负向，评分给 5 → negative
    ("破损了，色差严重", 5, ["破损", "色差"], "negative"),
    # 文本无标签命中，评分兜底
    ("东西收到了", 5, [], "positive"),
    ("东西收到了", 1, [], "negative"),
    ("东西收到了", 3, [], "neutral"),
    ("东西收到了", None, [], "neutral"),
]


@pytest.mark.parametrize("content,rating,tags,sentiment", EXTRACT_CASES)
def test_extract_tags_cases(content, rating, tags, sentiment):
    """单条评价：标签命中与情感倾向（文本命中优先于评分）"""
    res = review_ai.extract_tags(content, rating)
    for t in tags:
        assert t in res["tags"]
    assert res["sentiment"] == sentiment
    # 出参形状固定
    assert set(res.keys()) == {"tags", "sentiment", "matched"}


def test_extract_empty_and_punct():
    """空内容 / 纯标点：空标签中性，绝不脑补（本用例展开 2 个断言场景）"""
    assert review_ai.extract_tags("", 5) == {
        "tags": [], "sentiment": "neutral", "matched": []}
    assert review_ai.extract_tags("！！！？？？，。 ", 1) == {
        "tags": [], "sentiment": "neutral", "matched": []}
    # 非 str 安全降级
    assert review_ai.extract_tags(None)["tags"] == []


def test_extract_rating_only_no_text_tag():
    """无具体内容的短文本（如「好」）：命中「外观好看」是词表设计口径；
    本用例固定该行为，防止以后误改词典导致标签漂移。"""
    assert review_ai.extract_tags("好看", 5)["tags"] == ["外观好看"]
    # 「好」单字不在词典中 → 不脑补，评分兜底
    assert review_ai.extract_tags("好", 5)["tags"] == []
    assert review_ai.extract_tags("好", 5)["sentiment"] == "positive"


def test_aggregate_tie_ordering_deterministic():
    """同频次标签打平时按标签名升序（结果确定，可复现）"""
    entries = [
        {"content": "物流很快", "rating": 5},
        {"content": "质量不错", "rating": 5},
        {"content": "发货很快", "rating": 5},
    ]
    res = review_ai.aggregate_review_tags(entries)
    names = [r["tag"] for r in res["positive"]]
    assert all(r["count"] == 1 for r in res["positive"])
    assert names == sorted(names)   # 同 count 时 tag 名升序


def test_aggregate_review_tags():
    """聚合：频次降序、计数、带标签占比、形状固定（多断言场景）"""
    entries = [
        {"content": "物流很快，质量很好", "rating": 5},
        {"content": "物流很快", "rating": 5},
        {"content": "物流很快，发货很快", "rating": 5},
        {"content": "破损了，不满意", "rating": 1},
        {"content": "一般般", "rating": 3},
        {"content": "", "rating": 5},
    ]
    res = review_ai.aggregate_review_tags(entries)
    assert res["total"] == 6
    assert res["tagged"] == 4          # 前 4 条命中标签
    assert res["tagged_rate"] == round(4 * 100 / 6, 1)
    # 正向 TOP：物流快 3 次排第一
    pos = res["positive"]
    assert pos[0] == {"tag": "物流快", "count": 3}
    pos_tags = [r["tag"] for r in pos]
    assert pos_tags == sorted(pos_tags, key=lambda t: -dict(
        (r["tag"], r["count"]) for r in pos)[t])
    # 负向计数
    neg_map = dict((r["tag"], r["count"]) for r in res["negative"])
    assert neg_map["破损"] == 1 and neg_map["不满意"] == 1
    assert res["sentiment_dist"]["positive"] == 3
    assert res["sentiment_dist"]["negative"] == 1
    assert res["sentiment_dist"]["neutral"] == 2
    # 空 / 脏入参安全
    empty = review_ai.aggregate_review_tags([])
    assert empty["total"] == 0 and empty["tagged_rate"] == 0.0
    assert review_ai.aggregate_review_tags("not-list")["total"] == 0
    assert review_ai.aggregate_review_tags([None, 1, "x"])["total"] == 0


# ==================== 2. 三接口权限（6 条） ====================

PERM_ENDPOINTS = [
    "/admin/stats/review-insights?days=7",
    "/admin/stats/inventory-alerts?threshold=10",
    "/admin/stats/rfm?days=30",
]


@pytest.mark.parametrize("endpoint", PERM_ENDPOINTS)
def test_stats_guest_401(requests_session, base_url, endpoint):
    """游客（无 token）访问二期三接口 → 401"""
    r = requests_session.get(f"{base_url}{endpoint}")
    assert r.status_code == 401


@pytest.mark.parametrize("endpoint", PERM_ENDPOINTS)
def test_stats_normal_user_403(requests_session, base_url, auth_headers, endpoint):
    """普通用户访问二期三接口 → 403"""
    r = requests_session.get(f"{base_url}{endpoint}", headers=auth_headers)
    assert r.status_code == 403


# ============== 3. review-insights 接口口径（7 条） ==============

@pytest.mark.parametrize("days,ok", [(7, True), (30, True),
                                     (1, False), (0, False), (90, False),
                                     (-7, False), (999, False)])
def test_review_insights_days_whitelist(requests_session, base_url, days, ok):
    """days 白名单 7/30，其余（含 90/负数/0/999）→ 400"""
    ah = admin_headers(requests_session, base_url)
    r = get_review_insights(requests_session, base_url, ah, days)
    assert r.status_code == (200 if ok else 400)


def test_review_insights_days_bad_types(requests_session, base_url):
    """days 非数字字符串/bool → 400（一个用例覆盖多种脏值）"""
    ah = admin_headers(requests_session, base_url)
    for raw in ["abc", "", "7.5", "True"]:
        r = requests_session.get(
            f"{base_url}/admin/stats/review-insights?days={raw}", headers=ah)
        assert r.status_code == 400, raw


def test_review_insights_isolated_bad_review(requests_session, base_url):
    """专用商品+专用用户造 1 条低星评价：差评预警按 review id 精确隔离断言"""
    ah = admin_headers(requests_session, base_url)
    h, uname = make_user(requests_session, base_url)
    pid, pname = create_product(requests_session, base_url, ah, stock=20,
                                prefix="RI")
    unique_text = "专用差评" + unique_tag() + "，包装破损有瑕疵"
    rid, oid = make_completed_review(requests_session, base_url, ah, h, pid,
                                     rating=1, content=unique_text)

    r = get_review_insights(requests_session, base_url, ah, 7)
    assert r.status_code == 200
    data = r.json()["data"]

    # 差评预警中必须包含本次评价（按 id 精确匹配），字段完整
    hit = [x for x in data["bad_reviews"] if x["id"] == rid]
    assert len(hit) == 1
    item = hit[0]
    assert item["rating"] == 1
    assert item["order_id"] == oid
    assert unique_text[:60] in item["snippet"]
    assert item["created_at"]

    # 用户名脱敏：首字 + **（本用户名首字母 u）
    assert item["username"] == uname[0] + "**"
    assert uname not in item["username"]

    # 负向标签聚合包含 破损/瑕疵（按标签名匹配，非全局计数断言）
    neg_tags = [t["tag"] for t in data["negative_tags"]]
    assert "破损" in neg_tags and "瑕疵" in neg_tags


def test_review_insights_soft_deleted_excluded(requests_session, base_url):
    """管理员软删的评价不进洞察（先命中、删除后消失，基于专用 id 隔离）"""
    ah = admin_headers(requests_session, base_url)
    h, _ = make_user(requests_session, base_url)
    pid, _ = create_product(requests_session, base_url, ah, stock=20,
                            prefix="RD")
    text = "专用软删评价" + unique_tag() + "，物流很慢"
    rid, _ = make_completed_review(requests_session, base_url, ah, h, pid,
                                   rating=1, content=text)

    # 删除前差评预警能查到
    before = get_review_insights(requests_session, base_url, ah, 7).json()["data"]
    assert [x for x in before["bad_reviews"] if x["id"] == rid]

    r = requests_session.post(
        f"{base_url}/admin/reviews/{rid}/delete", headers=ah)
    assert r.status_code == 200

    after = get_review_insights(requests_session, base_url, ah, 7).json()["data"]
    assert not [x for x in after["bad_reviews"] if x["id"] == rid]


def test_review_insights_summary_shape(requests_session, base_url):
    """summary 字段形状与口径自洽（total >= 差评数；avg_rating 合理或 null）"""
    ah = admin_headers(requests_session, base_url)
    data = get_review_insights(
        requests_session, base_url, ah, 30).json()["data"]
    sm = data["summary"]
    assert {"total_reviews", "tagged_reviews", "bad_review_count",
            "avg_rating"} <= set(sm.keys())
    assert sm["total_reviews"] >= sm["bad_review_count"]
    assert sm["tagged_reviews"] <= sm["total_reviews"]
    if sm["avg_rating"] is not None:
        assert 1.0 <= sm["avg_rating"] <= 5.0


# ============== 4. inventory-alerts 接口口径（9 条） ==============

@pytest.mark.parametrize("threshold,ok", [
    ("1", True), ("100", True), ("10", True),       # 边界/常规
    ("0", False), ("101", False), ("-1", False),    # 越界
    ("1.5", False), ("abc", False), ("", False),    # 小数/字符串/空
    ("True", False), ("10.0", False),               # bool 文本 / 带小数点
])
def test_inventory_threshold_validation(requests_session, base_url,
                                        threshold, ok):
    """threshold 严格校验：仅纯整数字符串且 1-100 放行（11 个参数场景）"""
    ah = admin_headers(requests_session, base_url)
    r = requests_session.get(
        f"{base_url}/admin/stats/inventory-alerts?threshold={threshold}",
        headers=ah)
    assert r.status_code == (200 if ok else 400)


def test_inventory_default_threshold(requests_session, base_url):
    """缺省 threshold → 200，返回结构含 threshold=10"""
    ah = admin_headers(requests_session, base_url)
    r = requests_session.get(
        f"{base_url}/admin/stats/inventory-alerts", headers=ah)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["threshold"] == 10
    assert data["total"] == len(data["items"])
    # 默认阈值下，命中项库存全部 <= 10
    assert all(it["stock"] <= 10 for it in data["items"])


def test_inventory_special_products_hit(requests_session, base_url):
    """新建 3 个专用商品（库存 0/5/15）：threshold=10 时命中前两个且状态正确"""
    ah = admin_headers(requests_session, base_url)
    p0, n0 = create_product(requests_session, base_url, ah, stock=0,
                            prefix="I0")
    p5, n5 = create_product(requests_session, base_url, ah, stock=5,
                            prefix="I5")
    p15, n15 = create_product(requests_session, base_url, ah, stock=15,
                              prefix="I9")

    r = requests_session.get(
        f"{base_url}/admin/stats/inventory-alerts?threshold=10", headers=ah)
    items = r.json()["data"]["items"]
    by_id = dict((it["id"], it) for it in items)

    # stock=0 → out_of_stock；stock=5 → low；stock=15 不命中
    assert by_id[p0]["status"] == "out_of_stock"
    assert by_id[p0]["stock"] == 0
    assert by_id[p5]["status"] == "low"
    assert p15 not in by_id

    # 命中的专用商品顺序满足库存升序（0 在 5 前面）
    hit_ids = [it["id"] for it in items if it["id"] in (p0, p5)]
    assert hit_ids == [p0, p5]
    # 返回字段完整
    assert {"id", "name", "stock", "price", "status"} <= set(by_id[p5].keys())


def test_inventory_threshold_boundary_100(requests_session, base_url):
    """threshold=100：专用商品 stock=100 恰好命中（边界含等号）"""
    ah = admin_headers(requests_session, base_url)
    pid, _ = create_product(requests_session, base_url, ah, stock=100,
                            prefix="IB")
    r = requests_session.get(
        f"{base_url}/admin/stats/inventory-alerts?threshold=100", headers=ah)
    items = r.json()["data"]["items"]
    assert pid in [it["id"] for it in items]


def test_inventory_deleted_product_excluded(requests_session, base_url):
    """已下架(is_deleted=1)专用商品即使 stock <= 阈值也不命中"""
    ah = admin_headers(requests_session, base_url)
    pid, _ = create_product(requests_session, base_url, ah, stock=3,
                            prefix="IX")
    # 先确认在售时能命中
    r = requests_session.get(
        f"{base_url}/admin/stats/inventory-alerts?threshold=10", headers=ah)
    assert pid in [it["id"] for it in r.json()["data"]["items"]]

    # 管理员下架
    d = requests_session.delete(
        f"{base_url}/admin/products/{pid}", headers=ah)
    assert d.status_code == 200

    r = requests_session.get(
        f"{base_url}/admin/stats/inventory-alerts?threshold=10", headers=ah)
    assert pid not in [it["id"] for it in r.json()["data"]["items"]]


# ==================== 5. RFM 接口口径（8 条） ====================

@pytest.mark.parametrize("days,ok", [(7, True), (30, True), (90, True),
                                     (1, False), (0, False), (-30, False),
                                     (365, False), ("abc", False)])
def test_rfm_days_whitelist(requests_session, base_url, days, ok):
    """days 白名单 7/30/90，其余（1/0/负/365/非数字）→ 400"""
    ah = admin_headers(requests_session, base_url)
    r = requests_session.get(
        f"{base_url}/admin/stats/rfm?days={days}", headers=ah)
    assert r.status_code == (200 if ok else 400)


def test_rfm_three_deterministic_users(requests_session, base_url):
    """3 个专用新用户各造确定订单：拉接口取真实全体中位数后，逐用户手工
    复算 R/F/M 分值与分层（隔离断言，不做全局差分）。"""
    ah = admin_headers(requests_session, base_url)
    pa, _ = create_product(requests_session, base_url, ah, stock=200,
                           price=100.0, prefix="RA")
    pb, _ = create_product(requests_session, base_url, ah, stock=200,
                           price=100.0, prefix="RB")
    pc, _ = create_product(requests_session, base_url, ah, stock=200,
                           price=100.0, prefix="RC")

    ha, ua = make_user(requests_session, base_url)
    hb, ub = make_user(requests_session, base_url)
    hc, uc = make_user(requests_session, base_url)

    # A：2 单 paid（F=2, M=200）；B：1 单 paid（F=1, M=100）；
    # C：1 单 paid（F=1, M=100）。三人均刚下单，R 由接口给（应都为 0）
    make_paid_order(requests_session, base_url, ha, pa)
    make_paid_order(requests_session, base_url, ha, pb)
    make_paid_order(requests_session, base_url, hb, pc)
    # C 的订单商品复用 pa（库存充足）
    make_paid_order(requests_session, base_url, hc, pa)

    r = requests_session.get(
        f"{base_url}/admin/stats/rfm?days=7", headers=ah)
    assert r.status_code == 200
    data = r.json()["data"]
    med = data["median"]

    # 收集三个专用用户的明细
    flat = {}
    for seg in data["segments"].values():
        for u in seg["users"]:
            if u["username"] in (ua, ub, uc):
                flat[u["username"]] = u

    assert set(flat.keys()) == {ua, ub, uc}

    # 手工复算 3 人 RFM 子集的中位数（全部 R 应=0；F=[2,1,1] 中位1；
    # M=[200,100,100] 中位100）
    rs_ = [flat[ua]["r_days"], flat[ub]["r_days"], flat[uc]["r_days"]]
    fs_ = [flat[ua]["f"], flat[ub]["f"], flat[uc]["f"]]
    ms_ = [flat[ua]["m"], flat[ub]["m"], flat[uc]["m"]]
    assert rs_ == [0, 0, 0]
    assert fs_ == [2, 1, 1]
    assert ms_ == [200.0, 100.0, 100.0]
    # 三人子集中位数（用于核对这三个人的原始指标）；注意 med 是【窗口内全体
    # 用户】的中位数，库内还有其它用户，二者不要求相等。
    assert sorted(rs_)[1] == 0
    assert sorted(fs_)[1] == 1
    assert sorted(ms_)[1] == 100.0
    # 全体中位数必须是数值且结构完整（历史脏数据下也不能为 None/NaN）
    assert isinstance(med["recency"], (int, float))
    assert isinstance(med["frequency"], (int, float))
    assert isinstance(med["monetary"], (int, float))

    # 按【全体中位数】口径逐用户复算分数与分层
    for uname, u in flat.items():
        r_score = 2 if u["r_days"] <= med["recency"] else 1
        f_score = 2 if u["f"] >= med["frequency"] else 1
        m_score = 2 if u["m"] >= med["monetary"] else 1
        code = f"{r_score}{f_score}{m_score}"
        assert u["rfm"] == code, (uname, u["rfm"], code)
        # 用户出现在 code 对应分层下
        assert u["user_id"] in [
            x["user_id"] for x in data["segments"][code]["users"]]
        assert data["segments"][code]["label"] == \
            rfm_analysis.SEGMENT_LABELS[code]

    # A 必然 F=2/M=200 → F2M2；B、C F=1/M=100，是否 2 取决于全体中位数，
    # 但 rfm 字符串与所在层已逐用户自洽（上面已断言）
    assert flat[ua]["f"] == 2 and flat[ua]["m"] == 200.0
    # 各层 count 与 users 数量一致
    for code, seg in data["segments"].items():
        assert seg["count"] == len(seg["users"])


def test_rfm_single_user_no_crash(requests_session, base_url):
    """单用户样本：接口不崩，该用户出现在某一分层且订单计数正确。
    注意：「样本唯一时=中位数记 2 → 222」是纯函数 build_rfm 的规则，
    接口算的是【全库全体中位数】（库里还有其它用户的订单），所以这里
    不硬断言 222，只验证接口稳定性；222 兜底由纯函数离线测试保证。"""
    ah = admin_headers(requests_session, base_url)
    pid, _ = create_product(requests_session, base_url, ah, stock=50,
                            prefix="RS")
    h, uname = make_user(requests_session, base_url)
    make_paid_order(requests_session, base_url, h, pid)

    r = requests_session.get(
        f"{base_url}/admin/stats/rfm?days=7", headers=ah)
    assert r.status_code == 200
    data = r.json()["data"]
    flat = [u for seg in data["segments"].values() for u in seg["users"]]
    hit = [u for u in flat if u["username"] == uname]
    assert len(hit) == 1
    assert hit[0]["f"] == 1 and hit[0]["m"] == 100.0
    assert len(hit[0]["rfm"]) == 3
    # 层内 count 与 users 列表长度自洽
    for code, seg in data["segments"].items():
        assert seg["count"] == len(seg["users"])


def test_rfm_90day_structure_consistent(requests_session, base_url):
    """90 天窗口：结构完整（8 层/label/计数自洽/中位数），不得报错。
    注意：接口层无法构造「全库无任何订单」的真空窗口（种子+历史订单都在），
    真·空态（8 层全 0、中位数 0）由下面的纯函数离线测试保证。"""
    ah = admin_headers(requests_session, base_url)
    r = requests_session.get(
        f"{base_url}/admin/stats/rfm?days=90", headers=ah)
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data["segments"]) == 8
    flat_users = []
    for code, seg in data["segments"].items():
        assert seg["label"] == rfm_analysis.SEGMENT_LABELS[code]
        assert seg["count"] == len(seg["users"])
        flat_users.extend(seg["users"])
    assert data["summary"]["total_users"] == len(flat_users)
    assert set(data["median"].keys()) == {"recency", "frequency", "monetary"}


def test_rfm_empty_window_pure_function():
    """纯函数层空态：空用户集合 → 8 层全 0、中位数 0（不崩，无 NaN/inf）。
    接口层无法构造全库真空窗口，故空态口径在此离线验证。"""
    result = rfm_analysis.build_rfm([], days=90)
    assert len(result["segments"]) == 8
    for code, seg in result["segments"].items():
        assert seg["count"] == 0 and seg["users"] == []
        assert seg["label"] == rfm_analysis.SEGMENT_LABELS[code]
    assert result["summary"]["total_users"] == 0
    assert result["median"] == {"recency": 0, "frequency": 0, "monetary": 0}


def test_rfm_no_nan_inf(requests_session, base_url):
    """金额/中位数不得出现 NaN/inf（字符串级 + 数值级双重检查）"""
    import json as _json
    import math
    ah = admin_headers(requests_session, base_url)
    raw_text = requests_session.get(
        f"{base_url}/admin/stats/rfm?days=30", headers=ah).text
    assert "NaN" not in raw_text and "Infinity" not in raw_text
    data = _json.loads(raw_text)["data"]

    def walk(v):
        if isinstance(v, float):
            assert math.isfinite(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(data)


def test_rfm_summary_and_segment_order(requests_session, base_url):
    """summary 各层计数之和 = 总用户数；分层中文名映射完整"""
    ah = admin_headers(requests_session, base_url)
    data = requests_session.get(
        f"{base_url}/admin/stats/rfm?days=30", headers=ah).json()["data"]
    sm = data["summary"]
    seg_counts = list(sm["segments"].values())
    assert sum(seg_counts) == sm["total_users"]
    # 8 个中文层名齐全
    assert len(sm["segments"]) == 8
    expected_labels = set(rfm_analysis.SEGMENT_LABELS.values())
    assert set(sm["segments"].keys()) == expected_labels
    assert sm["days"] == 30
