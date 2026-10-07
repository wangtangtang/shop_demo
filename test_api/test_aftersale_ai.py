"""售后智能初审（人在回路 Human-in-the-loop）专项测试（0915）
================================================================
本文件共 16 个测试函数（参数化展开后共 28 条用例，pytest --collect-only 口径）。
全项目用例由 195 条增至 234 条（+39）。

功能：顾客提交售后申请时，纯 Python 规则引擎（app/aftersale_ai.py，离线可跑、
无网络/DB 依赖）自动给出 approve/reject/manual 三档“建议”与可解释理由，
建议随售后单落库（ai_suggestion / ai_reason / ai_analyzed_at），顾客申请响应、
订单详情、管理员售后列表三处都能看到。

核心安全边界（人在回路，本文件重点）：
- AI 建议 approve 绝不动工单状态：不调用 handle 时工单永远是 pending；
- AI 建议 approve 但管理员点拒绝 → 最终 rejected；
- AI 建议 reject 但管理员点同意 → 最终 approved；
  即【最终状态永远以管理员人工审核为唯一入口】。
- 建议字段不改变任何权限：普通用户访问审核接口仍是 403。

覆盖：
- 规则引擎纯函数：客观词→approve（3 参数化）/主观词→reject（2）/
  中性文本、空串、纯标点、长度<4（6 参数化）→manual；价保词仅在
  price_protect 类型下生效（2 参数化）；客观+主观同时命中的优先级（3 参数化）；
  命中词去重；返回结构固定。
- 接口三处可见：申请响应 / 订单详情 aftersale / 管理员列表。
- 健壮性：500 字长文本、emoji、SQL 注入串均不报错，结构正常。
"""
import pytest

from app.aftersale_ai import analyze_aftersale, SUGGESTION_TEXT
from test_logistics import make_user, admin_headers
from test_aftersale import shipped_order, apply_as, admin_aftersale_rows


# ================= 1. 规则引擎纯函数（不依赖服务/DB，断网也能跑） =================

@pytest.mark.parametrize("reason", [
    "收到的杯子破损了还有瑕疵",
    "商品坏了开不了机",
    "包裹漏发少发一件",
])
def test_rule_objective_suggests_approve(reason):
    """客观质量/履约问题词命中 → approve，matched 非空且理由可解释"""
    r = analyze_aftersale("refund_return", reason)
    assert r["suggestion"] == "approve"
    assert r["matched"], "客观词必须在 matched 中体现"
    assert "客观" in r["reason"] and "建议同意" in r["reason"]


@pytest.mark.parametrize("reason", [
    "单纯不喜欢了想退",
    "拍错型号了",
])
def test_rule_subjective_suggests_reject(reason):
    """纯买家主观原因词命中 → reject，理由里带“最终由管理员裁定”"""
    r = analyze_aftersale("refund_return", reason)
    assert r["suggestion"] == "reject"
    assert r["matched"]
    assert "主观" in r["reason"]
    assert "管理员裁定" in r["reason"]  # 文案上强调 AI 说了不算


@pytest.mark.parametrize("reason", [
    "想咨询一下售后政策",
    "联系商家处理",
])
def test_rule_neutral_suggests_manual(reason):
    """中性描述两类词都没命中 → manual"""
    r = analyze_aftersale("refund_return", reason)
    assert r["suggestion"] == "manual"
    assert r["matched"] == []


@pytest.mark.parametrize("reason", [
    "",            # 空串
    "   ",         # 纯空白
    "！！！？？",   # 纯标点
    "坏",          # 长度 1
    "坏了",        # 长度 2（命中词也不放行：有效信息不足 4 字）
    "abc",         # 长度 3 的英文
])
def test_rule_too_little_info_suggests_manual(reason):
    """空 / 纯标点 / 去标点后长度 < 4 → manual，理由固定为“申请信息过少”"""
    r = analyze_aftersale("refund_return", reason)
    assert r["suggestion"] == "manual"
    assert r["matched"] == []
    assert "信息过少" in r["reason"]


@pytest.mark.parametrize("atype,expect", [
    ("price_protect", "approve"),  # 价保类型：降价/差价算客观证据
    ("exchange", "manual"),        # 换货类型：价保词不生效，且无其他命中
])
def test_rule_price_protect_words_only_for_price_type(atype, expect):
    """“现在降价了有差价”只在 type=price_protect 时倾向同意"""
    r = analyze_aftersale(atype, "现在降价了有差价")
    assert r["suggestion"] == expect
    if expect == "approve":
        assert "降价" in r["matched"] and "差价" in r["matched"]


@pytest.mark.parametrize("reason,expect", [
    # 客观词数 >= 主观词数（打平也算客观优先）→ approve
    ("商品破损了，不过是我拍错了", "approve"),
    # 只有主观词 → reject
    ("单纯不喜欢了，拍错了想退", "reject"),
    # 两类都有但主观更多（1 客观 vs 3 主观）→ manual 证据冲突
    ("东西破损了但是我单纯不喜欢也不想要了拍错了", "manual"),
])
def test_rule_objective_subjective_priority(reason, expect):
    """客观词与主观词同时命中时的优先级规则"""
    r = analyze_aftersale("refund_return", reason)
    assert r["suggestion"] == expect, (reason, r)


def test_rule_matched_dedup_and_structure():
    """同一关键词重复出现只保留一次；返回结构固定 3 个键"""
    r = analyze_aftersale("refund_return", "坏了坏了坏了，真的坏了")
    assert set(r.keys()) == {"suggestion", "reason", "matched"}
    assert r["matched"] == ["坏了"]  # 去重且保规则表顺序
    assert r["suggestion"] in ("approve", "reject", "manual")
    assert SUGGESTION_TEXT["approve"] == "建议同意"
    assert SUGGESTION_TEXT["reject"] == "建议拒绝"
    assert SUGGESTION_TEXT["manual"] == "需人工核实"


def test_rule_is_pure_function():
    """纯函数：相同输入两次调用结果完全一致；入参为 None 不报错（接口兜底场景）"""
    a = analyze_aftersale("refund_return", "包裹漏发少发一件")
    b = analyze_aftersale("refund_return", "包裹漏发少发一件")
    assert a == b
    assert analyze_aftersale("refund_return", None)["suggestion"] == "manual"


# ================= 2. 接口三处可见：申请响应 / 订单详情 / 管理员列表 =================

def test_apply_response_carries_ai_suggestion(requests_session, base_url):
    """申请成功响应 data 带上建议三字段（客观原因 → approve）"""
    h = make_user(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=1)

    r = apply_as(requests_session, base_url, h, oid,
                 reason="收到的杯子破损了还有瑕疵")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["ai_suggestion"] == "approve"
    assert d["ai_suggestion_text"] == "建议同意"
    assert d["ai_reason"] and "破损" in d["ai_reason"]


def test_order_detail_shows_ai_fields(requests_session, base_url):
    """订单详情 aftersale 对象含 ai_suggestion / ai_suggestion_text / ai_reason"""
    h = make_user(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=2)
    apply_as(requests_session, base_url, h, oid, reason="商品坏了开不了机")

    detail = requests_session.get(f"{base_url}/orders/{oid}", headers=h).json()["data"]
    af = detail["aftersale"]
    assert af["ai_suggestion"] == "approve"
    assert af["ai_suggestion_text"] == "建议同意"
    assert af["ai_reason"]
    assert af["ai_analyzed_at"] is not None  # 分析时间已落库


def test_admin_aftersale_list_shows_ai_fields(requests_session, base_url):
    """管理员售后列表复用 aftersale_json，自动带出建议字段（主观原因 → reject）"""
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=3)
    apply_as(requests_session, base_url, h, oid, reason="单纯不喜欢了想退")

    rows = admin_aftersale_rows(requests_session, base_url, ah, oid)
    assert len(rows) == 1
    row = rows[0]
    assert row["ai_suggestion"] == "reject"
    assert row["ai_suggestion_text"] == "建议拒绝"
    assert "主观" in row["ai_reason"]


# ================= 3. 安全边界：人在回路（本文件重点） =================

def test_safety_ai_approve_keeps_pending_without_handle(requests_session, base_url):
    """①AI 建议 approve 后【不】调用 handle：工单 status 必须仍是 pending，
    不会自动通过"""
    h = make_user(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=4)
    r = apply_as(requests_session, base_url, h, oid, reason="商品坏了开不了机")
    rid = r.json()["data"]["id"]
    assert r.json()["data"]["ai_suggestion"] == "approve"

    # 不调用任何审核接口，隔一次查询状态依旧是 pending
    detail = requests_session.get(f"{base_url}/orders/{oid}", headers=h).json()["data"]
    assert detail["aftersale"]["id"] == rid
    assert detail["aftersale"]["status"] == "pending"
    assert detail["aftersale"]["handled_at"] is None


def test_safety_human_reject_overrides_ai_approve(requests_session, base_url):
    """②AI 建议 approve，但管理员点【拒绝】→ 最终 rejected（人工裁定为准）"""
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=5)
    rid = apply_as(requests_session, base_url, h, oid,
                   reason="包裹漏发少发一件，东西还破损了").json()["data"]["id"]

    r = requests_session.post(f"{base_url}/admin/aftersales/{rid}/handle",
                              headers=ah,
                              json={"action": "reject", "note": "凭证不足"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    assert r.json()["data"]["status"] == "rejected"

    detail = requests_session.get(f"{base_url}/orders/{oid}", headers=h).json()["data"]
    assert detail["aftersale"]["status"] == "rejected"
    # 建议字段仍然保留（建议归建议，结果归结果）
    assert detail["aftersale"]["ai_suggestion"] == "approve"


def test_safety_human_approve_overrides_ai_reject(requests_session, base_url):
    """③AI 建议 reject，但管理员点【同意】→ 最终 approved（人工裁定为准）"""
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=1)
    rid = apply_as(requests_session, base_url, h, oid,
                   reason="单纯不喜欢了想退").json()["data"]["id"]

    r = requests_session.post(f"{base_url}/admin/aftersales/{rid}/handle",
                              headers=ah, json={"action": "approve"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    assert r.json()["data"]["status"] == "approved"

    rows = admin_aftersale_rows(requests_session, base_url, ah, oid)
    row = rows[0]
    assert row["status"] == "approved"
    assert row["ai_suggestion"] == "reject"  # AI 当初建议拒绝，但人说了算


def test_safety_suggestion_does_not_change_permissions(requests_session, base_url):
    """④建议字段不改变任何权限：普通用户调审核接口依旧 403；
    调管理员列表依旧 403"""
    h = make_user(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=2)
    rid = apply_as(requests_session, base_url, h, oid, reason="商品破损了").json()["data"]["id"]

    r1 = requests_session.post(f"{base_url}/admin/aftersales/{rid}/handle",
                               headers=h, json={"action": "approve"})
    assert r1.status_code == 403
    r2 = requests_session.get(f"{base_url}/admin/aftersales", headers=h)
    assert r2.status_code == 403
    # 被 403 拦掉后工单仍是 pending
    detail = requests_session.get(f"{base_url}/orders/{oid}", headers=h).json()["data"]
    assert detail["aftersale"]["status"] == "pending"


# ================= 4. 健壮性：长文本 / emoji / 注入串 =================

def test_apply_long_emoji_injection_reason_ok(requests_session, base_url):
    """500 字长文本 + emoji + SQL 注入串：申请成功、建议结构正常，不 500、不脱库"""
    h = make_user(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=3)
    # 用循环保证正文 >=500 字，不依赖手算倍数（"商品破损漏发了"*60 只有 360 字）
    long_reason = "商品破损漏发了"
    while len(long_reason) < 500:
        long_reason += "商品破损漏发了"
    long_reason += " 😀🤖🔥 ' OR '1'='1\"; DROP TABLE after_sales; -- <script>alert(1)</script>"
    assert len(long_reason) >= 500

    r = apply_as(requests_session, base_url, h, oid, reason=long_reason)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["status"] == "pending"
    assert d["ai_suggestion"] in ("approve", "reject", "manual")
    assert d["ai_suggestion_text"] in ("建议同意", "建议拒绝", "需人工核实")
    assert isinstance(d["ai_reason"], str) and d["ai_reason"]

    # 订单详情能正常取回（含特殊字符），库表还在
    detail = requests_session.get(f"{base_url}/orders/{oid}", headers=h)
    assert detail.status_code == 200
    assert detail.json()["data"]["aftersale"]["ai_suggestion"] == d["ai_suggestion"]
