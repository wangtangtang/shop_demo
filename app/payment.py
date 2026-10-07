"""
支付模块（模拟支付网关）
==========================
真实电商支付链路：
    1. 统一下单  → 调支付平台接口，拿到支付参数/二维码
    2. 顾客扫码  → 在微信/支付宝 APP 里完成付款
    3. 异步回调  → 支付平台主动通知商家后端，后端【验签】后把订单改为已付款

本文件模拟第 1、3 步，业务流程与真实接入完全一致。
以后接支付宝沙箱/微信支付时，只需在 MockGateway 旁边
新增 AlipayGateway / WechatGateway（实现统一的 create_payment / verify_notify 两个方法），
业务代码（routes.py）不用动——这就是"网关适配模式"。
"""
import uuid
from datetime import datetime
from app import db
from app.models import Order

# 支持的支付渠道（真实系统里这里对应微信支付、支付宝两个网关适配器）
CHANNELS = {
    "wechat": {"name": "微信支付", "code": "WX"},
    "alipay": {"name": "支付宝", "code": "ZFB"},
}


def create_payment(order, channel):
    """统一下单：生成支付流水号，登记支付渠道。
    返回收银台需要的支付信息。对应真实系统中"调用支付平台下单接口"。"""
    if order.status != "pending":
        return None, f"订单当前状态[{Order.STATUS.get(order.status)}]，不能发起支付"
    if channel not in CHANNELS:
        return None, "不支持的支付方式"

    order.pay_channel = channel
    # 真实流水号由支付平台返回，这里按平台前缀+UUID模拟。
    # 每次发起支付都生成全新流水号：用户「返回重选支付方式」会先关闭旧支付单，
    # 新支付单生效后旧 trade_no 的迟到回调必须被识别为无效（trade_no 对不上）。
    order.trade_no = f"{CHANNELS[channel]['code']}{uuid.uuid4().hex[:16].upper()}"
    # 新支付单生效：关闭标记归零（旧支付单若被关闭过，从此刻起作废的是旧单）
    order.pay_closed = 0
    order.pay_closed_at = None
    db.session.commit()

    return {
        "channel": channel,
        "channel_name": CHANNELS[channel]["name"],
        "trade_no": order.trade_no,
        "amount": order.total_amount,
    }, None


def handle_paid(order, trade_no=None):
    """支付成功后的订单处理（幂等：重复回调不会重复加库存/改状态）。
    真实系统由支付平台的【异步回调】触发，入口在 routes.py 的 notify 接口。"""
    if order.status != "pending":
        return False  # 已支付/已取消的订单，忽略重复通知
    if order.pay_closed:
        return False  # 支付单已被用户主动关闭：旧二维码/迟到回调一律拒绝
    if trade_no and order.trade_no and trade_no != order.trade_no:
        return False  # 流水号对不上，拒绝（真实场景还要验签+验金额+验appid）

    order.status = "paid"          # 待支付 → 待发货
    order.paid_at = datetime.utcnow()
    db.session.commit()
    return True
