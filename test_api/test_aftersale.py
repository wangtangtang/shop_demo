"""售后申请 / 审核全链路 专项测试（0912 售后流程改版）
=====================================
本文件共 22 个测试函数（三种售后类型参数化后共 24 条用例）。

新流程：售后申请入口在【已发货(shipped)】阶段开放——确认收货和申请售后两个按钮
并存；申请售后(pending)后确认收货被冻结；管理员同意(approved)流程终态；管理员
拒绝(rejected)后两个按钮重新出现，用户可以重新申请售后（新建售后单，旧 rejected
单保留为历史）或确认收货（订单 completed 后售后入口关闭）。

覆盖点（顾客端）：
- 已发货(shipped)订单申请售后成功（返回 id、pending）；三种类型参数化
- 非法 type 400；原因为空（纯空白）400
- 状态门槛：待支付(pending) 400；已付款未发货(paid) 400（提示订单尚未发货）；
  已完成(completed，先 confirm) 400（提示已确认收货）
- pending 审核中重复申请 → 400；pending 状态下确认收货 → 400
- approved 后再次申请 → 400；approved 后确认收货 → 400
- rejected 后可【重新申请】：再 POST 成功，该订单共 2 张售后单，最新一张 pending、
  旧一张仍 rejected（管理员列表可查）
- rejected 后可【确认收货】：confirm 成功订单 completed；completed 后再申请 → 400
- 给别人的订单申请 → 404；未登录 → 401

覆盖点（管理端）：
- 售后列表能看到售后单（含类型/原因/订单号/用户名/金额）；普通用户 → 403
- approve → approved + handled_at 有值，顾客详情 status_text=已同意
- reject 带备注 → rejected + admin_note 顾客可见
- 处理已处理过的售后单 → 400
"""
import pytest

# 复用物流专项里的注册/登录/下单付款 helper（模块级函数）
from test_logistics import make_user, admin_headers, paid_order, ship_body
from conftest import create_address


def shipped_order(requests_session, base_url, headers, product_id=1):
    """链路 helper：建地址 → 下单 → 付款 → 管理员发货，
    返回 shipped（已发货、未确认收货）状态的 order_id。
    售后申请入口从 shipped 阶段开放，所以本文件大部分用例在此状态上操作。"""
    ah = admin_headers(requests_session, base_url)
    oid, _ = paid_order(requests_session, base_url, headers, product_id=product_id)
    r = requests_session.post(f"{base_url}/admin/orders/{oid}/ship", headers=ah,
                              json=ship_body())
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return oid


def apply_as(requests_session, base_url, headers, oid,
             atype="refund_return", reason="商品有问题想售后"):
    """发起售后申请，返回 response（用例自己断言状态码）"""
    return requests_session.post(f"{base_url}/orders/{oid}/aftersale", headers=headers,
                                 json={"type": atype, "reason": reason})


def admin_aftersale_rows(requests_session, base_url, ah, oid):
    """管理员售后列表里属于指定订单的全部售后单（含 rejected 历史单）"""
    r = requests_session.get(f"{base_url}/admin/aftersales?page=1&per_page=50",
                             headers=ah)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return [a for a in r.json()["data"]["items"] if a["order_id"] == oid]


def confirm(requests_session, base_url, headers, oid):
    """用户确认收货，返回 response"""
    return requests_session.post(f"{base_url}/orders/{oid}/action", headers=headers,
                                 json={"action": "confirm"})


# ---------- 顾客端：申请售后（shipped 状态开放） ----------

def test_apply_aftersale_ok(requests_session, base_url):
    """已发货(shipped)订单申请售后成功：返回 id，状态 pending"""
    h = make_user(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=1)

    r = apply_as(requests_session, base_url, h, oid,
                 reason="商品有划痕，想退货")
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    data = r.json()["data"]
    assert data["id"]
    assert data["status"] == "pending"

    # 订单详情里应能看到售后单（最新一张）
    detail = requests_session.get(f"{base_url}/orders/{oid}", headers=h).json()["data"]
    assert detail["aftersale"] is not None
    assert detail["aftersale"]["type_text"] == "退货退款"
    assert detail["aftersale"]["status_text"] == "审核中"
    assert detail["aftersale"]["reason"] == "商品有划痕，想退货"


@pytest.mark.parametrize("atype,text", [
    ("refund_return", "退货退款"),
    ("exchange", "换货"),
    ("price_protect", "价保"),
])
def test_apply_aftersale_three_types(requests_session, base_url, atype, text):
    """三种售后类型（退货退款/换货/价保）都能申请成功"""
    h = make_user(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=2)

    r = apply_as(requests_session, base_url, h, oid,
                 atype=atype, reason=f"{text}申请原因")
    assert r.status_code == 200 and r.json()["code"] == 0, r.text

    detail = requests_session.get(f"{base_url}/orders/{oid}", headers=h).json()["data"]
    assert detail["aftersale"]["type"] == atype
    assert detail["aftersale"]["type_text"] == text


def test_apply_aftersale_bad_type_400(requests_session, base_url):
    """非法售后类型 → 400"""
    h = make_user(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=3)

    r = apply_as(requests_session, base_url, h, oid,
                 atype="refund", reason="随便填的原因")
    assert r.status_code == 400
    assert r.json()["code"] == 400


def test_apply_aftersale_empty_reason_400(requests_session, base_url):
    """申请原因为空（纯空白）→ 400，提示请填写申请原因"""
    h = make_user(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=4)

    r = apply_as(requests_session, base_url, h, oid,
                 atype="exchange", reason="   ")
    assert r.status_code == 400
    assert "原因" in r.json()["msg"]


# ---------- 顾客端：状态门槛（只有 shipped 可申请） ----------

def test_apply_aftersale_on_pending_order_400(requests_session, base_url):
    """待支付（pending）订单申请售后 → 400"""
    h = make_user(requests_session, base_url)
    aid = create_address(requests_session, base_url, h)
    requests_session.post(f"{base_url}/cart", headers=h,
                          json={"product_id": 1, "quantity": 1})
    r = requests_session.post(f"{base_url}/orders", headers=h,
                              json={"address_id": aid})
    oid = r.json()["data"]["order_id"]  # 不付款，保持 pending

    r = apply_as(requests_session, base_url, h, oid,
                 atype="exchange", reason="订单还没付呢")
    assert r.status_code == 400


def test_apply_aftersale_on_paid_order_400(requests_session, base_url):
    """已付款但未发货（paid）申请售后 → 400，提示订单尚未发货"""
    h = make_user(requests_session, base_url)
    oid, _ = paid_order(requests_session, base_url, h, product_id=5)  # 付款后不发货

    r = apply_as(requests_session, base_url, h, oid, reason="还没发货想退")
    assert r.status_code == 400
    assert "发货" in r.json()["msg"]


def test_apply_aftersale_on_completed_order_400(requests_session, base_url):
    """已确认收货（completed，无售后单直接 confirm）申请售后 → 400，
    提示订单已确认收货，无法申请售后"""
    h = make_user(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=1)

    r = confirm(requests_session, base_url, h, oid)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    assert r.json()["data"]["status"] == "completed"

    r = apply_as(requests_session, base_url, h, oid, reason="收货后才发现问题")
    assert r.status_code == 400
    assert "确认收货" in r.json()["msg"]


# ---------- 顾客端：pending 审核中（重复申请 / 确认收货 都拦截） ----------

def test_apply_aftersale_twice_while_pending_400(requests_session, base_url):
    """售后审核中(pending)再次申请 → 400，提示审核中请勿重复提交"""
    h = make_user(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=2)

    r1 = apply_as(requests_session, base_url, h, oid, reason="第一次申请")
    assert r1.status_code == 200
    r2 = apply_as(requests_session, base_url, h, oid,
                  atype="exchange", reason="审核中再试一次")
    assert r2.status_code == 400
    assert "审核中" in r2.json()["msg"]


def test_confirm_while_aftersale_pending_400(requests_session, base_url):
    """售后审核中(pending)确认收货 → 400，提示等待审核结果；订单仍是 shipped"""
    h = make_user(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=3)
    apply_as(requests_session, base_url, h, oid, reason="申请售后中")

    r = confirm(requests_session, base_url, h, oid)
    assert r.status_code == 400
    assert "审核" in r.json()["msg"]

    detail = requests_session.get(f"{base_url}/orders/{oid}", headers=h).json()["data"]
    assert detail["status"] == "shipped"


# ---------- 顾客端：approved 已同意（重复申请 / 确认收货 都拦截） ----------

def test_apply_aftersale_again_after_approved_400(requests_session, base_url):
    """管理员同意(approved)后再次申请 → 400，提示售后已同意无需重复申请"""
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=4)
    rid = apply_as(requests_session, base_url, h, oid).json()["data"]["id"]

    r = requests_session.post(f"{base_url}/admin/aftersales/{rid}/handle",
                              headers=ah, json={"action": "approve"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text

    r = apply_as(requests_session, base_url, h, oid,
                 atype="exchange", reason="同意了还想再申请")
    assert r.status_code == 400
    assert "同意" in r.json()["msg"]


def test_confirm_after_aftersale_approved_400(requests_session, base_url):
    """售后已同意(approved)后确认收货 → 400，提示售后已同意无需确认收货"""
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=5)
    rid = apply_as(requests_session, base_url, h, oid,
                   atype="exchange", reason="尺码不合适想换货").json()["data"]["id"]
    requests_session.post(f"{base_url}/admin/aftersales/{rid}/handle",
                          headers=ah, json={"action": "approve"})

    r = confirm(requests_session, base_url, h, oid)
    assert r.status_code == 400
    assert "同意" in r.json()["msg"]

    detail = requests_session.get(f"{base_url}/orders/{oid}", headers=h).json()["data"]
    assert detail["status"] == "shipped"


# ---------- 顾客端：rejected 已拒绝（可重新申请 / 可确认收货） ----------

def test_reapply_after_rejected_ok(requests_session, base_url):
    """售后被拒绝(rejected)后可以重新申请：再 POST 成功；该订单共 2 张售后单，
    最新一张 pending、旧一张仍 rejected（旧单保留为历史，管理员列表可查）"""
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=1)
    rid1 = apply_as(requests_session, base_url, h, oid,
                    reason="第一次申请").json()["data"]["id"]

    # 管理员拒绝第一次申请
    r = requests_session.post(f"{base_url}/admin/aftersales/{rid1}/handle",
                              headers=ah,
                              json={"action": "reject", "note": "凭证不足，暂不支持"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text

    # rejected 不阻挡：重新申请成功
    r2 = apply_as(requests_session, base_url, h, oid,
                  atype="exchange", reason="补充凭证后重新申请")
    assert r2.status_code == 200 and r2.json()["code"] == 0, r2.text
    rid2 = r2.json()["data"]["id"]
    assert rid2 != rid1

    # 订单详情（最新一张）是新的 pending 单
    detail = requests_session.get(f"{base_url}/orders/{oid}", headers=h).json()["data"]
    assert detail["aftersale"]["id"] == rid2
    assert detail["aftersale"]["status"] == "pending"
    assert detail["aftersale"]["type"] == "exchange"

    # 管理员列表：该订单共 2 张售后单，旧 rejected + 新 pending 都在
    rows = admin_aftersale_rows(requests_session, base_url, ah, oid)
    assert len(rows) == 2, f"rejected 旧单应保留为历史，实际 {len(rows)} 张"
    by_id = {a["id"]: a for a in rows}
    assert by_id[rid1]["status"] == "rejected"
    assert by_id[rid2]["status"] == "pending"


def test_confirm_after_rejected_ok(requests_session, base_url):
    """售后被拒绝(rejected)后可以确认收货：confirm 成功、订单 completed；
    completed 后再申请售后 → 400（入口关闭）"""
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=2)
    rid = apply_as(requests_session, base_url, h, oid,
                   reason="申请价保").json()["data"]["id"]
    requests_session.post(f"{base_url}/admin/aftersales/{rid}/handle",
                          headers=ah,
                          json={"action": "reject", "note": "该商品不参与价保活动"})

    # rejected 后确认收货成功
    r = confirm(requests_session, base_url, h, oid)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    assert r.json()["data"]["status"] == "completed"

    # 已确认收货，售后入口关闭
    r = apply_as(requests_session, base_url, h, oid, reason="完成后又想申请")
    assert r.status_code == 400
    assert "确认收货" in r.json()["msg"]


# ---------- 顾客端：权限 / 登录 ----------

def test_apply_aftersale_others_order_404(requests_session, base_url):
    """给别人的订单申请售后 → 404（不暴露订单存在性）"""
    h1 = make_user(requests_session, base_url)
    h2 = make_user(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h1, product_id=3)

    r = apply_as(requests_session, base_url, h2, oid, reason="不是我的订单")
    assert r.status_code == 404


def test_apply_aftersale_not_login_401(requests_session, base_url):
    """未登录申请售后 → 401"""
    r = requests_session.post(f"{base_url}/orders/1/aftersale",
                              json={"type": "refund_return", "reason": "没登录"})
    assert r.status_code == 401


# ---------- 管理员端：售后列表 / 审核 ----------

def test_admin_aftersale_list_ok(requests_session, base_url):
    """管理员售后列表：能看到售后单，含类型/原因/订单号/用户名/金额"""
    h = make_user(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=4)
    apply_as(requests_session, base_url, h, oid,
             atype="price_protect", reason="商品降价了申请价保")

    ah = admin_headers(requests_session, base_url)
    r = requests_session.get(f"{base_url}/admin/aftersales?page=1&per_page=50",
                             headers=ah)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    pd = r.json()["data"]
    assert pd["total"] >= 1
    mine = [a for a in pd["items"] if a["order_id"] == oid]
    assert mine, "售后列表里应能找到刚申请的售后单"
    row = mine[0]
    assert row["type"] == "price_protect"
    assert row["type_text"] == "价保"
    assert row["status"] == "pending"
    assert row["reason"] == "商品降价了申请价保"
    assert row["order_id"] == oid
    assert row["username"]
    assert row["order_amount"] is not None


def test_admin_aftersale_list_requires_admin(requests_session, base_url):
    """普通用户调管理员售后列表 → 403"""
    h = make_user(requests_session, base_url)
    r = requests_session.get(f"{base_url}/admin/aftersales", headers=h)
    assert r.status_code == 403


def test_admin_approve_aftersale(requests_session, base_url):
    """管理员同意：status=approved、handled_at 有值；顾客详情 status_text=已同意"""
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=5)
    rid = apply_as(requests_session, base_url, h, oid,
                   atype="exchange", reason="尺码不合适想换货").json()["data"]["id"]

    r = requests_session.post(f"{base_url}/admin/aftersales/{rid}/handle",
                              headers=ah, json={"action": "approve"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    assert r.json()["data"]["status"] == "approved"

    # 列表里这条售后单 handled_at 有值
    listed = requests_session.get(f"{base_url}/admin/aftersales?page=1&per_page=50",
                                  headers=ah).json()["data"]["items"]
    row = [a for a in listed if a["id"] == rid][0]
    assert row["status"] == "approved"
    assert row["status_text"] == "已同意"
    assert row["handled_at"] is not None

    # 顾客订单详情看到「已同意」
    detail = requests_session.get(f"{base_url}/orders/{oid}", headers=h).json()["data"]
    assert detail["aftersale"]["status"] == "approved"
    assert detail["aftersale"]["status_text"] == "已同意"


def test_admin_reject_aftersale_with_note(requests_session, base_url):
    """管理员拒绝并填写备注：status=rejected，admin_note 顾客可见"""
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=1)
    rid = apply_as(requests_session, base_url, h, oid,
                   atype="price_protect", reason="申请价保").json()["data"]["id"]

    r = requests_session.post(f"{base_url}/admin/aftersales/{rid}/handle",
                              headers=ah,
                              json={"action": "reject", "note": "该商品不参与价保活动"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    assert r.json()["data"]["status"] == "rejected"

    detail = requests_session.get(f"{base_url}/orders/{oid}", headers=h).json()["data"]
    assert detail["aftersale"]["status"] == "rejected"
    assert detail["aftersale"]["status_text"] == "已拒绝"
    assert detail["aftersale"]["admin_note"] == "该商品不参与价保活动"


def test_admin_handle_aftersale_twice_400(requests_session, base_url):
    """处理已处理过的售后单 → 400"""
    h = make_user(requests_session, base_url)
    ah = admin_headers(requests_session, base_url)
    oid = shipped_order(requests_session, base_url, h, product_id=2)
    rid = apply_as(requests_session, base_url, h, oid, reason="想退货").json()["data"]["id"]

    r1 = requests_session.post(f"{base_url}/admin/aftersales/{rid}/handle",
                               headers=ah, json={"action": "approve"})
    assert r1.status_code == 200
    # 再处理一次（不管同意还是拒绝）→ 400
    r2 = requests_session.post(f"{base_url}/admin/aftersales/{rid}/handle",
                               headers=ah, json={"action": "reject", "note": "改主意了"})
    assert r2.status_code == 400
    assert "已处理" in r2.json()["msg"]
