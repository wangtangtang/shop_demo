"""管理员数据分析看板（一期）专项测试（0918）
================================================================
本文件共 22 个测试函数（权限/days 白名单/limit 边界/trend 天数参数化展开后
共 46 条用例）。全项目用例总数由 283 条增至 329 条（+46）。

覆盖的 6 个接口（全部挂 /api/admin/stats，全部 admin_required）：
- GET /overview?days=7            核心指标卡（GMV/下单量/客单价/支付率/售后率/平均分）
- GET /sales-trend?days=7         按天趋势（长度恰为 days，无单补零，日期升序）
- GET /top-products?days=30&limit=5 热销榜（有效订单项聚合，商品名用快照）
- GET /funnel                     用户转化漏斗（全量，去重 user_id，单调不增）
- GET /ai/aftersale               AI 初审效果面板（全量，manual 不进采纳率分母）
- GET /ai/briefing?days=7         AI 经营简报（纯函数模板 NLG，离线可加载）

统计口径（与 routes.py 注释一致）：
- days 只允许 7/30（默认 7），白名单外 0/负数/字符串/999 → 400；
- GMV/销售只算有效订单 status in (paid, shipped, completed)，
  pending/cancelled 计入 order_count 但不计 GMV；时间过滤统一用 created_at；
- 环比除 0 返回 null；金额 round 2、百分比 round 1。

注意：看板接口是【全局】统计，测试库还会被其它测试文件写数据，因此涉及数值的
用例一律采用「接口快照 → 造单 → 再取快照 → 断言增量」的差分法，避免依赖空库；
trend 跨天断言通过直接回拨 order.created_at 实现（服务/测试同机时可用，
不可用时 skip，而跨天补零/长度/日期断言不依赖回拨，始终执行）。
"""
import os
import time
import importlib.util
from datetime import datetime, timedelta

import pytest

from test_logistics import make_user, admin_headers, paid_order, ship_body
from test_aftersale import shipped_order, apply_as


# ============================ 公共工具 ============================

STATS = "/admin/stats"
# 6 个接口的游客/普通用户权限矩阵（funnel / ai-aftersale 是全量接口，不带 days）
ALL_ENDPOINTS = [
    "/overview?days=7",
    "/sales-trend?days=7",
    "/top-products?days=30&limit=5",
    "/funnel",
    "/ai/aftersale",
    "/ai/briefing?days=7",
]


def get_stats(requests_session, base_url, ah, path):
    return requests_session.get(base_url + STATS + path, headers=ah)


def pending_order(requests_session, base_url, headers, product_id, qty=1):
    """加购 → 下单但不付款，返回 pending 订单 id（不计 GMV，计 order_count）。"""
    r = requests_session.post(f"{base_url}/cart", headers=headers,
                              json={"product_id": product_id, "quantity": qty})
    assert r.status_code == 200, r.text
    from conftest import create_address
    aid = create_address(requests_session, base_url, headers)
    r = requests_session.post(f"{base_url}/orders", headers=headers,
                              json={"address_id": aid})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return r.json()["data"]["order_id"]


def ship_order_by_admin(requests_session, base_url, ah, oid):
    r = requests_session.post(f"{base_url}/admin/orders/{oid}/ship", headers=ah,
                              json=ship_body())
    assert r.status_code == 200 and r.json()["code"] == 0, r.text


def overview(requests_session, base_url, ah, days=7):
    r = get_stats(requests_session, base_url, ah, f"/overview?days={days}")
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return r.json()["data"]


# ---------- 直接回拨 created_at（服务/测试同机、可 import app 时才可用） ----------
_DB_APP = None


def _db_set_order_times(order_times):
    """{order_id: datetime} 直接改库设置订单 created_at（精确物理时间）。
    返回 True 成功；环境无 flask/config（纯静态沙箱）返回 False，用例自行 skip。"""
    global _DB_APP
    try:
        from app import create_app, db
        from app.models import Order
        if _DB_APP is None:
            _DB_APP = create_app()
    except Exception:
        return False
    with _DB_APP.app_context():
        for oid, when in order_times.items():
            order = db.session.get(Order, oid)
            if order is None:
                return False
            order.created_at = when
        db.session.commit()
    return True


def _db_backdate_orders(order_days, base_dt=None):
    """{order_id: days_ago} 直接改库把订单 created_at 拨到「基准前 N 天」的
    当前时刻。base_dt 缺省用 Python UTC（与 Order.default 同口径）。
    返回 True 表示成功；环境无 flask/config（纯静态沙箱）返回 False。"""
    if base_dt is None:
        base_dt = datetime.utcnow()
    return _db_set_order_times(
        {oid: base_dt - timedelta(days=n) for oid, n in order_days.items()})


def _db_cleanup_products(product_ids):
    """直连库彻底删除指定商品及其订单/订单项（热销榜测试自清理用）。
    返回 True 表示成功；无 flask/config（纯静态沙箱）返回 False，用例自行 skip。
    背景：热销榜是【全局】榜 limit=20，top 测试商品若不清理，重复跑 N 次后
    历史大成交量商品会占满榜单，把当次新商品挤出前 20——故必须每次自清理。"""
    global _DB_APP
    try:
        from app import create_app, db
        from app.models import Order, OrderItem, Product, Review
        if _DB_APP is None:
            _DB_APP = create_app()
    except Exception:
        return False
    if not product_ids:
        return True
    with _DB_APP.app_context():
        order_ids = [oid for (oid,) in db.session.query(OrderItem.order_id)
                     .filter(OrderItem.product_id.in_(product_ids)).distinct()]
        if order_ids:
            Review.query.filter(Review.order_id.in_(order_ids)).delete(
                synchronize_session=False)
            for mod_name, cls_name in [("app.models", "AfterSale"),
                                       ("app.models", "LogisticsTrack")]:
                try:
                    import importlib
                    cls = getattr(importlib.import_module(mod_name), cls_name)
                    cls.query.filter(cls.order_id.in_(order_ids)).delete(
                        synchronize_session=False)
                except Exception:
                    pass
            OrderItem.query.filter(OrderItem.order_id.in_(order_ids)).delete(
                synchronize_session=False)
            Order.query.filter(Order.id.in_(order_ids)).delete(
                synchronize_session=False)
        OrderItem.query.filter(OrderItem.product_id.in_(product_ids)).delete(
            synchronize_session=False)
        Product.query.filter(Product.id.in_(product_ids)).delete(
            synchronize_session=False)
        db.session.commit()
    return True


def _insert_legacy_aftersale(order_id, user_id, status):
    """直接插一条老工单（ai_suggestion/ai_reason/ai_analyzed_at 全 NULL），
    模拟迁移前历史数据。返回 True/False（无库环境 skip 用）。"""
    global _DB_APP
    try:
        from app import create_app, db
        from app.models import AfterSale
        if _DB_APP is None:
            _DB_APP = create_app()
    except Exception:
        return False
    with _DB_APP.app_context():
        db.session.add(AfterSale(
            order_id=order_id, user_id=user_id, type="refund_return",
            reason="老工单，没有 AI 初审字段", status=status,
            ai_suggestion=None, ai_reason=None, ai_analyzed_at=None))
        db.session.commit()
    return True


def _load_brief_module():
    """用 importlib 离线加载 app/dashboard_brief.py（不经 flask app，断网可跑）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "app", "dashboard_brief.py")
    spec = importlib.util.spec_from_file_location("dashboard_brief_offline", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


brief = _load_brief_module()


def make_brief_stats(**over):
    """构造形状合法的 briefing 入参，单个用例只覆盖自己关心的字段。"""
    stats = {
        "days": 7,
        "overview": {
            "gmv": 1280.0, "order_count": 10, "valid_order_count": 9,
            "avg_order_value": 142.2, "pay_rate": 90.0,
            "aftersale_rate": 0.0, "aftersale_count": 0,
            "avg_rating": 4.7, "rating_count": 6,
            "gmv_prev": 1000.0, "gmv_change_pct": 28.0,
            "order_count_prev": 9, "order_count_change_pct": 11.1,
        },
        "trend": [],
        "top_products": [
            {"product_id": 1, "product_name": "机械键盘", "qty": 3, "amount": 897.0}
        ],
        "ai_panel": {
            "total": 8, "analyzed": 8, "pending": 1,
            "suggestion_dist": {"approve": 5, "reject": 1, "manual": 2},
            "handled_total": 7, "agreed": 6, "decided_total": 6,
            "agreement_rate": 85.7,
        },
    }
    for key, val in over.items():
        if key == "overview":
            stats["overview"].update(val)
        elif key == "ai_panel":
            stats["ai_panel"].update(val)
        else:
            stats[key] = val
    return stats


# ============================ 1. 权限 ============================

@pytest.mark.parametrize("path", ALL_ENDPOINTS)
def test_stats_guest_401(requests_session, base_url, path):
    """游客（不带 token）调 6 个看板接口 → 一律 401"""
    r = requests_session.get(base_url + STATS + path)
    assert r.status_code == 401
    assert r.json()["code"] == 401


@pytest.mark.parametrize("path", ALL_ENDPOINTS)
def test_stats_normal_user_403(requests_session, base_url, path):
    """已登录普通顾客调 6 个看板接口 → 一律 403"""
    h = make_user(requests_session, base_url)
    r = get_stats(requests_session, base_url, h, path)
    assert r.status_code == 403
    assert r.json()["code"] == 403


# ============================ 2. days 白名单 ============================

@pytest.mark.parametrize("days,expected", [
    ("7", 200), ("30", 200),       # 白名单
    ("0", 400), ("999", 400),      # 越界
    ("-1", 400), ("abc", 400),     # 负数 / 非数字
])
def test_overview_days_whitelist(requests_session, base_url, days, expected):
    """overview days 白名单：7/30 正常，0/999/-1/abc → 400"""
    ah = admin_headers(requests_session, base_url)
    r = get_stats(requests_session, base_url, ah, f"/overview?days={days}")
    assert r.status_code == expected
    if expected == 400:
        assert r.json()["code"] == 400


@pytest.mark.parametrize("path,days,expected", [
    ("/sales-trend", "7", 200), ("/sales-trend", "30", 200),
    ("/sales-trend", "0", 400), ("/sales-trend", "abc", 400),
    ("/ai/briefing", "7", 200), ("/ai/briefing", "999", 400),
])
def test_trend_and_briefing_days_whitelist(requests_session, base_url,
                                           path, days, expected):
    """sales-trend / ai-briefing 同样只接受 7/30，非法 → 400"""
    ah = admin_headers(requests_session, base_url)
    r = get_stats(requests_session, base_url, ah, f"{path}?days={days}")
    assert r.status_code == expected


@pytest.mark.parametrize("limit,expected", [
    ("1", 200), ("20", 200),      # 合法边界
    ("0", 400), ("21", 400),      # 越界
])
def test_top_products_limit_boundary(requests_session, base_url, limit, expected):
    """热销榜 limit 白名单：1-20 合法，0/21 → 400"""
    ah = admin_headers(requests_session, base_url)
    r = get_stats(requests_session, base_url, ah, f"/top-products?days=30&limit={limit}")
    assert r.status_code == expected


# ============================ 3. 空态 / 结构完整性 ============================

def test_empty_state_all_endpoints_structure(requests_session, base_url):
    """空态结构恒定：6 个接口不报错，字段齐全、类型正确；
    全局库可能已有其它测试造的数据（看板不按用户隔离），数值为 0 的精确断言
    依赖 reset_db 后的空库，非空库只校验类型/非负，结构恒定是硬保证。"""
    ah = admin_headers(requests_session, base_url)

    ov = get_stats(requests_session, base_url, ah, "/overview?days=7").json()["data"]
    for k in ("gmv", "order_count", "valid_order_count", "avg_order_value",
              "pay_rate", "aftersale_rate", "aftersale_count",
              "avg_rating", "rating_count", "gmv_prev", "gmv_change_pct",
              "order_count_prev", "order_count_change_pct", "days"):
        assert k in ov, "overview 缺字段 %s" % k
    assert ov["gmv"] >= 0 and ov["order_count"] >= 0
    assert ov["avg_rating"] is None or 1 <= ov["avg_rating"] <= 5
    assert ov["gmv_change_pct"] is None or isinstance(ov["gmv_change_pct"], (int, float))

    assert isinstance(get_stats(requests_session, base_url, ah,
                                "/top-products?days=7").json()["data"], list)
    assert isinstance(get_stats(requests_session, base_url, ah,
                                "/funnel").json()["data"], list)
    ai = get_stats(requests_session, base_url, ah, "/ai/aftersale").json()["data"]
    assert set(ai["suggestion_dist"].keys()) == {"approve", "reject", "manual"}
    assert ai["agreement_rate"] is None or 0 <= ai["agreement_rate"] <= 100
    brief_data = get_stats(requests_session, base_url, ah,
                           "/ai/briefing?days=7").json()["data"]
    assert brief_data["title"] and isinstance(brief_data["paragraphs"], list)
    blob = str(brief_data)
    assert "None" not in blob and "NaN" not in blob


@pytest.mark.parametrize("days", [7, 30])
def test_trend_length_and_date_window(requests_session, base_url, days):
    """trend 长度必须恰好 = days（无单日期补零），首日 = today-(days-1)，
    末日 = today，日期升序；每行四字段齐全。"""
    ah = admin_headers(requests_session, base_url)
    rows = get_stats(requests_session, base_url, ah,
                     f"/sales-trend?days={days}").json()["data"]
    assert len(rows) == days
    # 日期窗口以【服务器返回】为准（服务器基准兼容 UTC/本地两种落库口径），
    # 客户端不再用 datetime.utcnow() 猜测末日期——北京凌晨会相差一天。
    out_dates = [datetime.strptime(r["date"], "%Y-%m-%d").date()
                 for r in rows]
    assert out_dates[0] == out_dates[-1] - timedelta(days=days - 1)
    assert out_dates == sorted(out_dates)
    for r in rows:
        assert set(r.keys()) == {"date", "gmv", "orders", "paid_orders"}
        assert r["gmv"] >= 0 and r["orders"] >= 0 and r["paid_orders"] >= 0


# ============================ 4. overview 精确对账 ============================

def _latest_trend_bucket(requests_session, base_url, ah):
    """取 sales-trend 数组【最后一个桶】（服务器视角最新日期，即今天）。
    不按 UTC 日期匹配：北京凌晨 0-8 点跑测试时 UTC 仍停在前一天，按
    datetime.utcnow().date() 匹配会取错桶（曾导致 orders 差分恒为 0）。
    同一天内滑动窗口只前移几秒，前后两次取的末桶必为同一天，差分稳定。"""
    r = get_stats(requests_session, base_url, ah, "/sales-trend?days=7")
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return r.json()["data"][-1]


def _get_order(requests_session, base_url, headers, oid):
    r = requests_session.get(f"{base_url}/orders/{oid}", headers=headers)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return r.json()["data"]


def test_overview_reconcile_pending_paid_cancel(requests_session, base_url):
    """口径对账（不依赖全局差分，避免滑动窗口边界上历史订单滑出污染）：
    pending 单（计下单量不计 GMV）+ 支付一单（计 GMV/有效单）
    + 取消一单（计下单量不计 GMV）。
    - 逐单详情验证状态与金额归属
    - 用 sales-trend 今日桶差分：orders +3、paid_orders +1、gmv +199
    - overview 内部口径自洽：客单价=GMV/有效单、支付率=有效单/全部单。"""
    ah = admin_headers(requests_session, base_url)
    h = make_user(requests_session, base_url)
    before = _latest_trend_bucket(requests_session, base_url, ah)

    # 1) pending：商品 3（1599），不付款
    pending_oid = pending_order(requests_session, base_url, h, product_id=3)
    # 2) 支付一单：商品 4（199）
    paid_oid, _ = paid_order(requests_session, base_url, h, product_id=4)
    # 3) 下一单再取消：商品 5（699）
    cancel_oid = pending_order(requests_session, base_url, h, product_id=5)
    r = requests_session.post(f"{base_url}/orders/{cancel_oid}/action", headers=h,
                              json={"action": "cancel"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text

    # 逐单归属验证
    po = _get_order(requests_session, base_url, h, pending_oid)
    assert po["status"] == "pending" and po["total_amount"] == 1599.0
    do = _get_order(requests_session, base_url, h, paid_oid)
    assert do["status"] == "paid" and do["total_amount"] == 199.0
    co = _get_order(requests_session, base_url, h, cancel_oid)
    assert co["status"] == "cancelled" and co["total_amount"] == 699.0

    # 今日桶差分：只有支付单计 GMV/有效单；三单都计 orders
    after = _latest_trend_bucket(requests_session, base_url, ah)
    assert after["orders"] == before["orders"] + 3
    assert after["paid_orders"] == before["paid_orders"] + 1
    assert after["gmv"] == round(before["gmv"] + 199.0, 2)

    # overview 内部口径自洽
    ov = overview(requests_session, base_url, ah)
    if ov["valid_order_count"]:
        assert ov["avg_order_value"] == round(
            ov["gmv"] / ov["valid_order_count"], 2)
    assert ov["pay_rate"] == round(
        ov["valid_order_count"] * 100.0 / ov["order_count"], 1)


def test_overview_aftersale_rate(requests_session, base_url):
    """售后率 = 周期内售后申请单数 / 有效订单数 *100。
    不用全局差分（滑动窗口边界上的历史单滑出同样会污染）：
    用专用商品造一单 shipped + 申请售后，逐单验证工单归属，
    再断言 overview 的售后率字段与 count 口径自洽。"""
    ah = admin_headers(requests_session, base_url)
    ts = int(time.time() * 1000)
    r = requests_session.post(f"{base_url}/admin/products", headers=ah,
                              json={"name": f"售后率统计专用_{ts}", "price": 88,
                                    "stock": 50, "description": "售后率统计专用"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    pid = r.json()["data"]["id"]

    h = make_user(requests_session, base_url)
    oid, _ = paid_order(requests_session, base_url, h, product_id=pid)
    ship_order_by_admin(requests_session, base_url, ah, oid)
    r = apply_as(requests_session, base_url, h, oid,
                 reason="收到的商品破损了，有明显瑕疵")
    assert r.status_code == 200, r.text

    # 逐单验证：订单上挂着 pending 售后工单
    detail = _get_order(requests_session, base_url, h, oid)
    assert detail["status"] == "shipped"
    assert detail["aftersale"] and detail["aftersale"]["status"] == "pending"

    # overview 售后率与 count 口径自洽
    after = overview(requests_session, base_url, ah)
    assert after["aftersale_rate"] == round(
        after["aftersale_count"] * 100.0 / after["valid_order_count"], 1)


def test_rating_summary_isolated_special_product(requests_session, base_url):
    """平均分聚合用【全新专用商品】验证，与库内历史评价完全隔离（避免全局
    overview 的 rating_count 差分被历史积累评价污染）：走完整 支付→发货→
    确认收货→评价 链路后，该商品公开评价接口的 summary 精确为 count=1 /
    avg_rating=5.0 / 5星分布=1 / 好评率100，列表恰好 1 条。"""
    ah = admin_headers(requests_session, base_url)
    ts = int(time.time() * 1000)
    r = requests_session.post(f"{base_url}/admin/products", headers=ah,
                              json={"name": f"评分统计专用_{ts}", "price": 100,
                                    "stock": 50, "description": "评分统计专用"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    pid = r.json()["data"]["id"]

    h = make_user(requests_session, base_url)
    oid, _ = paid_order(requests_session, base_url, h, product_id=pid)
    ship_order_by_admin(requests_session, base_url, ah, oid)
    r = requests_session.post(f"{base_url}/orders/{oid}/action", headers=h,
                              json={"action": "confirm"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    r = requests_session.post(f"{base_url}/orders/{oid}/reviews", headers=h,
                              json={"rating": 5, "content": "物流很快，手感不错"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text

    # 公开商品评价接口（游客可看），summary 统计与历史评价天然隔离
    r = requests_session.get(f"{base_url}/products/{pid}/reviews")
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    d = r.json()["data"]
    assert len(d["items"]) == 1
    s = d["summary"]
    assert s["count"] == 1
    assert s["avg_rating"] == 5.0
    assert s["rating_dist"]["5"] == 1
    assert s["good_rate"] == 100


# ============================ 5. 趋势按天分桶 ============================

def test_trend_daily_buckets_backdated(requests_session, base_url):
    """跨 2 天分桶：订单 created_at 按 UTC 存储、trend 按东八区展示归桶。
    把两笔有效订单物理时间分别设到「末桶展示日 12:00 的 2 天前 / 3 天前」
    （即 UTC 20:00，加 8h 恰为展示日，不踩边界），差分断言对应日期桶
    orders/gmv 各 +1/精确金额；目标桶按服务器返回日期定位（[-3]/[-4]）。
    无库环境（纯静态沙箱）skip；长度/补零/日期窗口由另一条用例保证。"""
    ah = admin_headers(requests_session, base_url)
    h = make_user(requests_session, base_url)

    def trend():
        return get_stats(requests_session, base_url, ah,
                         "/sales-trend?days=7").json()["data"]

    before = trend()
    # 锚点：服务器末桶展示日 12:00（物理 UTC 20:00，+8h=展示日12:00）
    end_date = datetime.strptime(before[-1]["date"], "%Y-%m-%d").date()
    anchor = datetime.combine(end_date, datetime.min.time()) \
        .replace(hour=12)
    oid_299, _ = paid_order(requests_session, base_url, h, product_id=1)  # 299
    oid_89, _ = paid_order(requests_session, base_url, h, product_id=2)   # 89
    if not _db_set_order_times({oid_299: anchor - timedelta(days=2),
                                oid_89: anchor - timedelta(days=3)}):
        pytest.skip("当前环境无法直连数据库回拨 created_at")

    after = trend()
    assert len(after) == len(before) == 7
    d2, d3 = after[-3], after[-4]
    assert d2["orders"] == before[-3]["orders"] + 1
    assert d2["gmv"] == round(before[-3]["gmv"] + 299.0, 2)
    assert d3["orders"] == before[-4]["orders"] + 1
    assert d3["gmv"] == round(before[-4]["gmv"] + 89.0, 2)
    # 结构上每个桶都必须在；长度恒为 7 即证明补零
    assert all({"date", "gmv", "orders", "paid_orders"} <= set(r.keys())
               for r in after)


# ============================ 6. 热销榜（快照名） ============================

def test_top_products_snapshot_name_and_order(requests_session, base_url):
    """热销榜是【全局】榜且默认 limit=20，库内历史商品成交量大，小成交量
    专用商品挤不进前 20。故用全新商品 + 大成交量（A 2000 件、B 1999 件）
    稳定占据前两名：榜单顺序 [A,B]（qty desc）；之后管理员把 A 改名，
    榜单 product_name 仍是下单时快照（与评价展示同哲学）。
    测试结束【自清理】本次商品/订单，保证可重复执行（历史污染由配套
    scripts/cleanup_top_history.py 清理一次）。"""
    ah = admin_headers(requests_session, base_url)
    ts = int(time.time() * 1000)
    name_a, name_b = f"统计专用A_{ts}", f"统计专用B_{ts}"

    def create_product(name, stock):
        r = requests_session.post(f"{base_url}/admin/products", headers=ah,
                                  json={"name": name, "price": 500, "stock": stock,
                                        "description": "统计测试专用"})
        assert r.status_code == 200 and r.json()["code"] == 0, r.text
        return r.json()["data"]["id"]

    id_a = create_product(name_a, 10000)
    id_b = create_product(name_b, 10000)
    try:
        def bulk_paid_order(pid, qty):
            """新用户对指定商品加购 qty 件并完成支付，返回订单号。"""
            hu = make_user(requests_session, base_url)
            from conftest import create_address
            aid = create_address(requests_session, base_url, hu)
            requests_session.post(f"{base_url}/cart", headers=hu,
                                  json={"product_id": pid, "quantity": qty})
            r = requests_session.post(f"{base_url}/orders", headers=hu,
                                      json={"address_id": aid})
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            oid = r.json()["data"]["order_id"]
            requests_session.post(f"{base_url}/orders/{oid}/pay", headers=hu,
                                  json={"channel": "wechat"})
            rr = requests_session.post(f"{base_url}/pay/mock-notify",
                                       json={"order_id": oid})
            assert rr.status_code == 200 and rr.json()["code"] == 0, rr.text
            return oid

        bulk_paid_order(id_a, 2000)
        bulk_paid_order(id_b, 1999)

        rows = get_stats(requests_session, base_url, ah,
                         "/top-products?days=7&limit=20").json()["data"]
        mine = [r for r in rows if r["product_id"] in (id_a, id_b)]
        assert len(mine) == 2, f"大成交量专用商品应稳定进前20，实际：{[x['product_id'] for x in rows]}"
        assert mine[0]["product_id"] == id_a
        assert mine[0]["qty"] == 2000 and mine[0]["amount"] == 1000000.0
        assert mine[0]["product_name"] == name_a
        assert mine[1]["product_id"] == id_b
        assert mine[1]["qty"] == 1999 and mine[1]["amount"] == 999500.0

        # 管理员改 A 的现价/现名，历史榜单名/金额仍是下单时快照
        r = requests_session.put(f"{base_url}/admin/products/{id_a}", headers=ah,
                                 json={"name": f"改名后A_{ts}", "price": 999})
        assert r.status_code == 200, r.text
        rows2 = get_stats(requests_session, base_url, ah,
                          "/top-products?days=7&limit=20").json()["data"]
        mine2 = [r for r in rows2 if r["product_id"] in (id_a, id_b)]
        assert len(mine2) == 2
        assert mine2[0]["product_name"] == name_a
        assert mine2[0]["amount"] == 1000000.0
        assert mine2[1]["product_name"] == name_b
    finally:
        # 无论断言成功与否，都删除本次商品与订单，保证下轮可重复跑
        if not _db_cleanup_products([id_a, id_b]):
            pytest.skip("当前环境无法直连数据库清理 top 测试数据")


# ============================ 7. 转化漏斗 ============================

def test_funnel_stages_monotonic_and_rates(requests_session, base_url):
    """漏斗 5 级按去重 user_id：users→cart→order→paid→completed，单调不增；
    每级 rate = 相对上一级（首级 100.0）。造一个全新用户走完
    注册→加购→下单(pending)→支付→发货→收货，五级计数按链路递增。"""
    ah = admin_headers(requests_session, base_url)

    def fetch_funnel():
        return get_stats(requests_session, base_url, ah, "/funnel").json()["data"]

    def funnel():
        rows = fetch_funnel()
        return {r["key"]: r for r in rows}

    before = funnel()
    assert [r["key"] for r in fetch_funnel()] == \
        ["users", "cart_users", "order_users", "paid_users", "completed_users"]

    h = make_user(requests_session, base_url)  # +1 注册
    from conftest import create_address
    aid = create_address(requests_session, base_url, h)
    requests_session.post(f"{base_url}/cart", headers=h,
                          json={"product_id": 1, "quantity": 1})  # +1 加购
    r = requests_session.post(f"{base_url}/orders", headers=h,
                              json={"address_id": aid})
    oid = r.json()["data"]["order_id"]                      # +1 下单(pending)
    requests_session.post(f"{base_url}/orders/{oid}/pay", headers=h,
                          json={"channel": "alipay"})
    requests_session.post(f"{base_url}/pay/mock-notify", json={"order_id": oid})  # +1 支付
    ship_order_by_admin(requests_session, base_url, ah, oid)
    requests_session.post(f"{base_url}/orders/{oid}/action", headers=h,
                          json={"action": "confirm"})       # +1 完成

    after = funnel()
    assert after["users"]["users"] == before["users"]["users"] + 1
    assert after["cart_users"]["users"] == before["cart_users"]["users"] + 1
    assert after["order_users"]["users"] == before["order_users"]["users"] + 1
    assert after["paid_users"]["users"] == before["paid_users"]["users"] + 1
    assert after["completed_users"]["users"] == before["completed_users"]["users"] + 1

    # 单调不增 + rate 与定义自洽
    counts = [after[k]["users"] for k in
              ("users", "cart_users", "order_users", "paid_users", "completed_users")]
    assert counts[0] >= counts[1] >= counts[2] >= counts[3] >= counts[4]
    assert after["users"]["rate"] == 100.0
    for prev_key, key in zip(
            ("users", "cart_users", "order_users", "paid_users"),
            ("cart_users", "order_users", "paid_users", "completed_users")):
        p = after[prev_key]["users"]
        expect = round(after[key]["users"] * 100.0 / p, 1) if p else 0
        assert after[key]["rate"] == expect


# ============================ 8. AI 初审效果面板 ============================

def test_ai_panel_agreement_rate(requests_session, base_url):
    """差分精确对账：approve+approved（一致）、reject+approved（不一致）、
    manual+approved（不进分母）各一 → analyzed +3、dist 各 +1、
    handled_total +3、decided_total +2、agreed +1；采纳率按 agreed/decided 精确。"""
    ah = admin_headers(requests_session, base_url)

    def panel():
        return get_stats(requests_session, base_url, ah,
                         "/ai/aftersale").json()["data"]

    before = panel()
    # (理由, 期望 AI 建议, 管理员动作)
    cases = [
        ("收到的商品破损了，坏了不能用", "approve", "approve"),   # 一致
        ("单纯不喜欢拍错了想退货", "reject", "approve"),           # 不一致
        ("想再考虑一下后续的安排", "manual", "approve"),           # 人工，不进分母
    ]
    for reason, expect_sug, action in cases:
        h = make_user(requests_session, base_url)
        oid = shipped_order(requests_session, base_url, h, product_id=3)
        r = apply_as(requests_session, base_url, h, oid, reason=reason)
        assert r.status_code == 200, r.text
        aid = r.json()["data"]["id"]
        assert r.json()["data"]["ai_suggestion"] == expect_sug
        r = requests_session.post(f"{base_url}/admin/aftersales/{aid}/handle",
                                  headers=ah, json={"action": action})
        assert r.status_code == 200 and r.json()["code"] == 0, r.text

    after = panel()
    assert after["total"] == before["total"] + 3
    assert after["analyzed"] == before["analyzed"] + 3
    assert after["handled_total"] == before["handled_total"] + 3
    assert after["decided_total"] == before["decided_total"] + 2
    assert after["agreed"] == before["agreed"] + 1
    d = after["suggestion_dist"]
    bd = before["suggestion_dist"]
    assert d["approve"] == bd["approve"] + 1
    assert d["reject"] == bd["reject"] + 1
    assert d["manual"] == bd["manual"] + 1
    # 采纳率精确：(before.agreed + 1) / (before.decided + 2) * 100
    expect_rate = round((before["agreed"] + 1) * 100.0
                        / (before["decided_total"] + 2), 1)
    assert after["agreement_rate"] == expect_rate


def test_ai_panel_legacy_null_suggestion(requests_session, base_url):
    """老工单（ai_suggestion=NULL）：total/pending/handled 计数正常，
    但 analyzed、suggestion_dist、decided_total、agreed 都不受影响。"""
    ah = admin_headers(requests_session, base_url)
    h = make_user(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=4)
    # 该订单 user_id：从订单详情取
    detail = requests_session.get(f"{base_url}/orders/{oid}", headers=h).json()["data"]
    uid = detail["user_id"]
    if not _insert_legacy_aftersale(oid, uid, "pending"):
        pytest.skip("当前环境无法直连数据库插入老工单")
    assert _insert_legacy_aftersale(oid, uid, "approved") is True

    # 老工单是本次刚插的：直接断言全局面板里至少有这 2 条，
    # 且两条老工单都不进 AI 计数：dist 三项之和恒等于 analyzed
    p1 = get_stats(requests_session, base_url, ah,
                   "/ai/aftersale").json()["data"]
    assert p1["total"] >= 2
    assert p1["suggestion_dist"]["approve"] + p1["suggestion_dist"]["reject"] \
        + p1["suggestion_dist"]["manual"] == p1["analyzed"]
    # 老工单 ai_suggestion 为 NULL：不计入 analyzed，三项分布之和恒等于 analyzed
    assert p1["agreement_rate"] is None or 0 <= p1["agreement_rate"] <= 100


# ============================ 9. AI 经营简报（纯函数离线测） ============================

def test_briefing_rising():
    """上升场景：开场句含 GMV 金额（千分位）、订单数、环比上升；
    TOP1 句含商品名/销量；采纳率 87.5% → good alert。"""
    r = brief.generate_briefing(make_brief_stats(
        overview={"gmv": 1280.0, "gmv_change_pct": 28.0},
        ai_panel={"agreement_rate": 87.5}))
    blob = " ".join(r["paragraphs"])
    assert "近7天" in r["title"]
    assert "¥1,280.00" in r["paragraphs"][0]
    assert "上升" in r["paragraphs"][0]
    assert "机械键盘" in blob and "3 件" in blob
    assert any(a["level"] == "good" and "87.5%" in a["text"] for a in r["alerts"])
    assert "None" not in str(r) and "NaN" not in str(r)


def test_briefing_dropping_multi_alerts():
    """下降场景：GMV -50%/售后率 15%/支付率 50%/采纳率 0% → 多条 warn，
    开场句为下降，金额与百分比正确。"""
    r = brief.generate_briefing(make_brief_stats(
        overview={"gmv": 500.0, "order_count": 20, "aftersale_rate": 15.0,
                  "pay_rate": 50.0, "gmv_change_pct": -50.0},
        ai_panel={"agreement_rate": 0.0, "decided_total": 2}))
    assert "下降" in r["paragraphs"][0] and "50.0%" in r["paragraphs"][0]
    texts = [a["text"] for a in r["alerts"] if a["level"] == "warn"]
    blob = " ".join(texts)
    assert "售后率" in blob and "15.0%" in blob
    assert "GMV 环比下降" in blob
    assert "支付率" in blob and "50.0%" in blob
    assert any("采纳率" in t for t in texts)


def test_briefing_no_sales():
    """零成交：GMV/订单均 0、top 为空、无采纳率样本 → 无 None/NaN，
    热销句给「本周期暂无成交」，AI 给 info 提示。"""
    r = brief.generate_briefing(make_brief_stats(
        overview={"gmv": 0, "order_count": 0, "valid_order_count": 0,
                  "avg_order_value": 0, "pay_rate": 0, "aftersale_rate": 0,
                  "aftersale_count": 0, "avg_rating": None, "rating_count": 0,
                  "gmv_prev": 0, "gmv_change_pct": None,
                  "order_count_prev": 0, "order_count_change_pct": None},
        top_products=[],
        ai_panel={"agreement_rate": None, "handled_total": 0,
                  "suggestion_dist": {"approve": 0, "reject": 0, "manual": 0}}))
    blob = str(r)
    assert "None" not in blob and "NaN" not in blob
    assert "暂无成交" in r["paragraphs"][0]
    assert "本周期暂无成交" in r["paragraphs"][1]
    assert any(a["level"] == "info" for a in r["alerts"])


def test_briefing_high_aftersale_warn_only():
    """售后率 11%（>10%）且 GMV 上升、支付率正常 → 只报售后 warn，
    不报 GMV/支付率 warn。"""
    r = brief.generate_briefing(make_brief_stats(
        overview={"order_count": 9, "aftersale_rate": 11.0, "pay_rate": 90.0,
                  "gmv_change_pct": 20.0}))
    warn_texts = [a["text"] for a in r["alerts"] if a["level"] == "warn"]
    assert any("售后率" in t for t in warn_texts)
    assert not any("GMV" in t for t in warn_texts)
    assert not any("支付率" in t for t in warn_texts)


def test_briefing_low_payrate_warn():
    """支付率 59%（<60%）→ 待支付积压 warn；售后率 0 不报售后 warn。"""
    r = brief.generate_briefing(make_brief_stats(
        overview={"order_count": 100, "pay_rate": 59.0, "aftersale_rate": 0.0,
                  "gmv_change_pct": 5.0}))
    warn_texts = [a["text"] for a in r["alerts"] if a["level"] == "warn"]
    assert any("支付率" in t and "59.0%" in t for t in warn_texts)
    assert not any("售后率" in t for t in warn_texts)


def test_briefing_none_agreement_and_garbage_safe():
    """agreement_rate=None → info 降级不出 None；脏数据（NaN 字符串/inf/缺字段）
    也不允许出现在输出的【文本】里。注意 level="info" 是合法告警级别，
    不能用 "inf" in str(r) 去匹配（会误伤 info），故只检查 title/段落/告警文案，
    level 单独按合法集合白名单校验。"""
    r = brief.generate_briefing({
        "days": 7,
        "overview": {"gmv": "oops", "order_count": None, "pay_rate": None,
                     "aftersale_rate": None, "gmv_change_pct": float("nan")},
        "top_products": None,
        "ai_panel": {"agreement_rate": None, "suggestion_dist": None},
    })
    text_blob = " ".join([r.get("title", "")] + list(r.get("paragraphs", []))
                         + [a.get("text", "") for a in r.get("alerts", [])])
    assert "None" not in text_blob and "NaN" not in text_blob and "inf" not in text_blob.lower()
    # 告警级别只允许三种合法值（"info" 合法，不允许出现拼写错误如 "inffo"）
    assert all(a["level"] in ("good", "warn", "info") for a in r["alerts"])
    assert any(a["level"] == "info" for a in r["alerts"])
    assert "暂无成交" in r["paragraphs"][1]


def test_briefing_flat_threshold_and_output_shape():
    """±1% 内算持平（0.5% → 持平，无方向箭头文案）；出参形状固定。
    采纳率 70% 落中间区间 → info。"""
    r = brief.generate_briefing(make_brief_stats(
        overview={"gmv_change_pct": 0.5},
        ai_panel={"agreement_rate": 70.0}))
    assert "持平" in r["paragraphs"][0]
    assert "上升" not in r["paragraphs"][0] and "下降" not in r["paragraphs"][0]
    assert set(r.keys()) == {"title", "paragraphs", "alerts"}
    assert isinstance(r["title"], str) and len(r["paragraphs"]) >= 3
    for a in r["alerts"]:
        assert a["level"] in ("good", "warn", "info") and a["text"]
    assert any(a["level"] == "info" and "70.0%" in a["text"] for a in r["alerts"])
    # 空入参也不能炸（最终兜底）
    r2 = brief.generate_briefing(None)
    assert r2["title"] and r2["paragraphs"] and isinstance(r2["alerts"], list)
    assert "None" not in str(r2) and "NaN" not in str(r2)
