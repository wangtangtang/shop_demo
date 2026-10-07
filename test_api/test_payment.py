"""支付模块测试（模拟微信/支付宝网关）

覆盖：
- 统一下单（微信/支付宝）
- 模拟异步回调把订单改为已支付
- 回调幂等（重复通知不改状态、不报错）
- 异常：伪造流水号、非法渠道、已支付订单不能重复支付、越权操作他人订单
- 支付单可关闭（0917）：关闭/幂等关闭/非 pending 拒绝/越权 404/关闭后回调被拒/
  重新发起支付生成新流水号且新回调正常（修复"返回重选后从订单页再进又回到旧渠道扫码页"）

本文件原 10 条 + 0917 追加支付单关闭 7 条 = 共 17 条用例。
"""
import time


def _new_order(headers, requests_session, base_url, qty=1):
    """加购并下单（先建收货地址），返回 order_id"""
    # 下单必须带 address_id：用 conftest 的 create_address 给本用户建一条
    from conftest import create_address
    address_id = create_address(requests_session, base_url, headers)
    requests_session.post(f"{base_url}/cart",
                          json={"product_id": 1, "quantity": qty}, headers=headers)
    r = requests_session.post(f"{base_url}/orders", json={"address_id": address_id},
                              headers=headers)
    return r.json()["data"]["order_id"]


# ---------- 统一下单 ----------
def test_pay_create_wechat(auth_headers, requests_session, base_url):
    oid = _new_order(auth_headers, requests_session, base_url)
    r = requests_session.post(f"{base_url}/orders/{oid}/pay",
                              json={"channel": "wechat"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["channel"] == "wechat"
    assert body["data"]["channel_name"] == "微信支付"
    assert body["data"]["trade_no"].startswith("WX")


def test_pay_create_alipay(auth_headers, requests_session, base_url):
    oid = _new_order(auth_headers, requests_session, base_url)
    r = requests_session.post(f"{base_url}/orders/{oid}/pay",
                              json={"channel": "alipay"}, headers=auth_headers)
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["channel"] == "alipay"
    assert body["data"]["trade_no"].startswith("ZFB")


def test_pay_create_invalid_channel(auth_headers, requests_session, base_url):
    oid = _new_order(auth_headers, requests_session, base_url)
    r = requests_session.post(f"{base_url}/orders/{oid}/pay",
                              json={"channel": "bitcoin"}, headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["code"] == 400


def test_pay_requires_login(requests_session, base_url):
    """未登录不能发起支付"""
    r = requests_session.post(f"{base_url}/orders/1/pay", json={"channel": "wechat"})
    assert r.status_code == 401


# ---------- 模拟异步回调 ----------
def test_mock_notify_marks_order_paid(auth_headers, requests_session, base_url):
    oid = _new_order(auth_headers, requests_session, base_url)
    requests_session.post(f"{base_url}/orders/{oid}/pay",
                          json={"channel": "wechat"}, headers=auth_headers)
    # 模拟支付平台回调（不带登录 token，回调本来就是服务器对服务器）
    r = requests_session.post(f"{base_url}/pay/mock-notify",
                              json={"order_id": oid})
    assert r.status_code == 200
    assert r.json()["code"] == 0

    # 订单状态应变为 paid(待发货)
    r = requests_session.get(f"{base_url}/orders", headers=auth_headers)
    order = [o for o in r.json()["data"]["items"] if o["id"] == oid][0]
    assert order["status"] == "paid"
    assert order["pay_channel"] == "wechat"
    assert order["paid_at"] is not None


def test_mock_notify_is_idempotent(auth_headers, requests_session, base_url):
    """支付平台可能重复推送回调，第二次必须安全忽略，不报错"""
    oid = _new_order(auth_headers, requests_session, base_url)
    requests_session.post(f"{base_url}/orders/{oid}/pay",
                          json={"channel": "alipay"}, headers=auth_headers)
    r1 = requests_session.post(f"{base_url}/pay/mock-notify", json={"order_id": oid})
    r2 = requests_session.post(f"{base_url}/pay/mock-notify", json={"order_id": oid})
    assert r1.json()["code"] == 0
    assert r2.json()["code"] == 0  # 重复通知同样返回成功

    r = requests_session.get(f"{base_url}/orders", headers=auth_headers)
    order = [o for o in r.json()["data"]["items"] if o["id"] == oid][0]
    assert order["status"] == "paid"


def test_mock_notify_wrong_trade_no_rejected(auth_headers, requests_session, base_url):
    """伪造的流水号不能让订单变成已支付（真实场景对应验签失败）"""
    oid = _new_order(auth_headers, requests_session, base_url)
    requests_session.post(f"{base_url}/orders/{oid}/pay",
                          json={"channel": "wechat"}, headers=auth_headers)
    requests_session.post(f"{base_url}/pay/mock-notify",
                          json={"order_id": oid, "trade_no": "FAKE_TRADE_NO_XXX"})

    r = requests_session.get(f"{base_url}/orders", headers=auth_headers)
    order = [o for o in r.json()["data"]["items"] if o["id"] == oid][0]
    assert order["status"] == "pending"  # 仍是待支付，伪造回调被拒


def test_paid_order_cannot_pay_again(auth_headers, requests_session, base_url):
    """已支付订单重复发起支付 → 400"""
    oid = _new_order(auth_headers, requests_session, base_url)
    requests_session.post(f"{base_url}/orders/{oid}/pay",
                          json={"channel": "wechat"}, headers=auth_headers)
    requests_session.post(f"{base_url}/pay/mock-notify", json={"order_id": oid})

    r = requests_session.post(f"{base_url}/orders/{oid}/pay",
                              json={"channel": "wechat"}, headers=auth_headers)
    assert r.status_code == 400


def test_pay_others_order_forbidden(auth_headers, requests_session, base_url):
    """不能给别人的订单发起支付（越权 → 404，不暴露订单存在性）"""
    # 自己下一单
    oid = _new_order(auth_headers, requests_session, base_url)
    # 注册另一个用户
    username = f"payuser_{int(time.time())}"
    requests_session.post(f"{base_url}/register",
                          json={"username": username, "password": "123456"})
    login = requests_session.post(f"{base_url}/login",
                                  json={"username": username, "password": "123456"})
    other_token = login.json()["data"]["token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}

    r = requests_session.post(f"{base_url}/orders/{oid}/pay",
                              json={"channel": "wechat"}, headers=other_headers)
    assert r.status_code == 404


# ---------- 支付单关闭（0917：收银台「返回重选支付方式」） ----------
def _pay_pending_order(headers, requests_session, base_url, channel="wechat", qty=1):
    """下单并发起支付（不模拟回调），返回 (order_id, trade_no)：订单仍 pending、
    支付单有效。pay-status / cancel 系列用例在此状态上操作。"""
    oid = _new_order(headers, requests_session, base_url, qty=qty)
    r = requests_session.post(f"{base_url}/orders/{oid}/pay",
                              json={"channel": channel}, headers=headers)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return oid, r.json()["data"]["trade_no"]


def test_pay_cancel_pending_ok(auth_headers, requests_session, base_url):
    """pending 单主动关闭支付单：成功、pay_closed=1；pay-status 能查到关闭标记"""
    oid, _ = _pay_pending_order(auth_headers, requests_session, base_url)
    r = requests_session.post(f"{base_url}/orders/{oid}/pay/cancel",
                              headers=auth_headers)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text

    r = requests_session.get(f"{base_url}/orders/{oid}/pay-status",
                             headers=auth_headers)
    data = r.json()["data"]
    assert data["pay_closed"] == 1
    assert data["status"] == "pending"  # 订单本身仍是待支付，只是支付单作废

    # 订单详情同步带关闭标记
    r = requests_session.get(f"{base_url}/orders/{oid}", headers=auth_headers)
    assert r.json()["data"]["pay_closed"] == 1


def test_pay_cancel_idempotent(auth_headers, requests_session, base_url):
    """重复关闭幂等：第二次仍返回成功，不报错"""
    oid, _ = _pay_pending_order(auth_headers, requests_session, base_url)
    r1 = requests_session.post(f"{base_url}/orders/{oid}/pay/cancel",
                               headers=auth_headers)
    r2 = requests_session.post(f"{base_url}/orders/{oid}/pay/cancel",
                               headers=auth_headers)
    assert r1.json()["code"] == 0
    assert r2.status_code == 200 and r2.json()["code"] == 0, r2.text


def test_pay_cancel_paid_order_400(auth_headers, requests_session, base_url):
    """非 pending（已支付 paid / 已发货 shipped）订单不能取消支付 → 400"""
    oid, _ = _pay_pending_order(auth_headers, requests_session, base_url)
    requests_session.post(f"{base_url}/pay/mock-notify", json={"order_id": oid})

    r = requests_session.post(f"{base_url}/orders/{oid}/pay/cancel",
                              headers=auth_headers)
    assert r.status_code == 400
    assert "不能取消支付" in r.json()["msg"]


def test_pay_cancel_others_order_404(auth_headers, requests_session, base_url):
    """不能关闭别人的支付单（越权 → 404，不暴露订单存在性）"""
    oid, _ = _pay_pending_order(auth_headers, requests_session, base_url)
    username = f"cancel_{int(time.time())}"
    requests_session.post(f"{base_url}/register",
                          json={"username": username, "password": "123456"})
    login = requests_session.post(f"{base_url}/login",
                                  json={"username": username, "password": "123456"})
    other_headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}

    r = requests_session.post(f"{base_url}/orders/{oid}/pay/cancel",
                              headers=other_headers)
    assert r.status_code == 404


def test_pay_cancel_requires_login(requests_session, base_url):
    """未登录不能关闭支付单 → 401"""
    r = requests_session.post(f"{base_url}/orders/1/pay/cancel")
    assert r.status_code == 401


def test_notify_rejected_after_pay_cancel(auth_headers, requests_session, base_url):
    """核心安全用例：关闭旧微信支付单后，旧二维码/迟到回调必须被拒绝，
    订单保持 pending（防止关单后被旧回调置成已付款）。"""
    oid, old_trade_no = _pay_pending_order(auth_headers, requests_session, base_url,
                                           channel="wechat")
    # 用户在收银台点「返回重选支付方式」→ 关闭当前支付单
    r = requests_session.post(f"{base_url}/orders/{oid}/pay/cancel",
                              headers=auth_headers)
    assert r.json()["code"] == 0

    # 旧二维码对应的支付平台回调到达：不带/带旧流水号两种形式都必须拒
    r1 = requests_session.post(f"{base_url}/pay/mock-notify",
                               json={"order_id": oid})
    r2 = requests_session.post(f"{base_url}/pay/mock-notify",
                               json={"order_id": oid, "trade_no": old_trade_no})
    assert r1.status_code == 400 and r1.json()["notify_result"] == "fail"
    assert r2.status_code == 400 and r2.json()["notify_result"] == "fail"
    assert r1.json()["msg"] == "支付单已关闭"

    r = requests_session.get(f"{base_url}/orders/{oid}", headers=auth_headers)
    assert r.json()["data"]["status"] == "pending"


def test_repay_after_cancel_new_trade_no_and_notify_ok(auth_headers, requests_session,
                                                       base_url):
    """关闭后重新发起支付（换渠道）：生成全新 trade_no、pay_closed 归 0，
    新支付单的回调能正常把订单置成 paid；且旧 trade_no 与新单不同。"""
    oid, old_trade_no = _pay_pending_order(auth_headers, requests_session, base_url,
                                           channel="wechat")
    requests_session.post(f"{base_url}/orders/{oid}/pay/cancel", headers=auth_headers)

    # 重新选支付宝下单
    r = requests_session.post(f"{base_url}/orders/{oid}/pay",
                              json={"channel": "alipay"}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    new_info = r.json()["data"]
    assert new_info["channel"] == "alipay"
    assert new_info["trade_no"].startswith("ZFB")
    assert new_info["trade_no"] != old_trade_no  # 新支付单新流水号

    # 关闭标记已归零，pay-status 体现
    r = requests_session.get(f"{base_url}/orders/{oid}/pay-status",
                             headers=auth_headers)
    assert r.json()["data"]["pay_closed"] == 0

    # 新支付单回调正常落账
    r = requests_session.post(f"{base_url}/pay/mock-notify",
                              json={"order_id": oid})
    assert r.status_code == 200 and r.json()["code"] == 0
    r = requests_session.get(f"{base_url}/orders/{oid}", headers=auth_headers)
    order = r.json()["data"]
    assert order["status"] == "paid"
    assert order["pay_channel"] == "alipay"


# ---------- 后台订单列表带支付渠道（需求2 回归） ----------
def _admin_headers(requests_session, base_url):
    resp = requests_session.post(f"{base_url}/login",
                                 json={"username": "admin", "password": "admin123"})
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


def _find_admin_order(requests_session, base_url, ah, oid):
    """后台订单列表已分页：翻完全部页找到指定订单"""
    page = 1
    while True:
        pd = requests_session.get(
            f"{base_url}/admin/orders",
            params={"page": page, "per_page": 50}, headers=ah).json()["data"]
        for o in pd["items"]:
            if o["id"] == oid:
                return o
        if page >= pd["total_pages"]:
            return None
        page += 1


def test_admin_orders_show_pay_channel(auth_headers, requests_session, base_url):
    """后台订单分页接口的每个订单对象都带 pay_channel 字段：
    微信支付后显示 wechat、支付宝支付后显示 alipay、待支付订单为空串"""
    ah = _admin_headers(requests_session, base_url)

    # 微信支付一单
    oid_wx = _new_order(auth_headers, requests_session, base_url)
    requests_session.post(f"{base_url}/orders/{oid_wx}/pay",
                          json={"channel": "wechat"}, headers=auth_headers)
    requests_session.post(f"{base_url}/pay/mock-notify", json={"order_id": oid_wx})
    order = _find_admin_order(requests_session, base_url, ah, oid_wx)
    assert order is not None, "后台订单列表找不到刚支付的订单"
    assert order["pay_channel"] == "wechat"

    # 支付宝支付一单
    oid_zfb = _new_order(auth_headers, requests_session, base_url)
    requests_session.post(f"{base_url}/orders/{oid_zfb}/pay",
                          json={"channel": "alipay"}, headers=auth_headers)
    requests_session.post(f"{base_url}/pay/mock-notify", json={"order_id": oid_zfb})
    order = _find_admin_order(requests_session, base_url, ah, oid_zfb)
    assert order is not None
    assert order["pay_channel"] == "alipay"

    # 待支付订单（只下单没付款）：pay_channel 为空字符串，前端显示 "-"
    oid_pending = _new_order(auth_headers, requests_session, base_url)
    order = _find_admin_order(requests_session, base_url, ah, oid_pending)
    assert order is not None
    assert order["status"] == "pending"
    assert order["pay_channel"] == ""
