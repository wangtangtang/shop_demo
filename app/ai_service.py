"""
AI 服务层（智能客服 + 智能推荐）
================================
与支付模块（payment.py）相同的「网关适配模式」：

- 默认使用本地规则引擎（MockEngine）：
  * 离线可跑，不依赖任何大模型 API key，答辩断网也不怕
  * 输出固定的意图标签，自动化测试可以稳定断言
    （真大模型每次回答都不同，没法写 assert——这是 AI 测试的基本常识）
- 预留大模型适配器位：以后接通义千问/DeepSeek 等国内大模型时，
  只需新增一个 LLMEngine 实现 chat() 方法，routes.py 业务代码不用动。

客服能力：意图识别 → 查真实业务数据（订单/商品/库存）→ 生成回复；
安全上做了 prompt 注入拦截和水平越权防护（用户只能查自己的订单）。
"""
from app.models import Product, Cart, Order

# 意图标签（测试断言 intent 字段，不纠结具体措辞——这是 AI 测试的关键思路）
INTENT_ORDER = "order_status"      # 查订单/物流
INTENT_PRODUCT = "product_query"   # 查商品/库存/价格
INTENT_RECOMMEND = "recommend"     # 求推荐
INTENT_REFUND = "refund"           # 退货售后
INTENT_GREETING = "greeting"       # 打招呼
INTENT_SAFETY = "safety_refuse"    # 安全拦截（prompt 注入 / 越权 / 套系统信息）
INTENT_FALLBACK = "fallback"       # 兜底

# prompt 注入 / 越权 / 套取系统信息的关键词（命中即安全拒绝）
INJECTION_KEYWORDS = [
    "忽略之前", "忽略上面", "忽略以上", "ignore previous", "ignore above",
    "系统提示", "system prompt", "你的指令", "管理员密码", "admin密码",
    "admin123", "别人的订单", "其他人的订单", "别人订单", "所有用户",
    "数据库密码", "root密码", "后台密码",
]

REFUND_REPLY = (
    "退货政策：商品未拆封支持 7 天无理由退换；质量问题 15 天内包换。"
    "你可以在「我的订单」里申请售后，或工作时间联系人工客服处理~"
)

GREETING_REPLY = (
    "你好呀！我是极客商城智能助手 🤖 可以问我：\n"
    "· 商品库存/价格（如「键盘还有货吗」）\n"
    "· 我的订单到哪了\n"
    "· 想买点什么，求推荐\n"
    "· 退货售后政策"
)

FALLBACK_REPLY = (
    "这个问题我还没学会😅 你可以试试问我：「键盘多少钱」「我的订单到哪了」"
    "「推荐点东西」，复杂问题也可以联系人工客服~"
)

SAFETY_REPLY = (
    "这个请求我不能帮你处理哦。我只能查询你自己的订单和商城商品信息，"
    "系统配置、管理员账号和其他人的信息都无权透露，如有需要请联系管理员。"
)

# 品类词与配套关系（「猜你喜欢」推荐用）
CATEGORY_WORDS = ["键盘", "鼠标", "显示器", "扩展坞", "耳机"]
COMPLEMENT = {
    "键盘": ["鼠标", "扩展坞", "耳机"],
    "鼠标": ["键盘", "扩展坞", "耳机"],
    "显示器": ["键盘", "鼠标", "扩展坞"],
    "扩展坞": ["键盘", "鼠标", "耳机"],
    "耳机": ["键盘", "鼠标", "扩展坞"],
}


def _snippets(text):
    """取文本的连续 2 字片段（去空格/短横线、转小写），用于商品名模糊匹配"""
    s = text.replace(" ", "").replace("-", "").replace("　", "").lower()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _find_products(message, limit=3):
    """按商品名片段在用户消息中命中商品（后台新增商品自动支持，不用改关键词）"""
    msg_pieces = _snippets(message)
    hits = []
    for p in Product.query.filter_by(is_deleted=0).all():
        if msg_pieces & _snippets(p.name):
            hits.append(p)
        if len(hits) >= limit:
            break
    return hits


def _detect_intent(message):
    msg = message.lower()
    # 1. 安全拦截最优先（prompt 注入 / 越权 / 套系统信息）
    if any(kw in msg for kw in INJECTION_KEYWORDS):
        return INTENT_SAFETY
    # 2. 退货售后
    if any(w in msg for w in ["退货", "退款", "售后", "换货", "返修"]):
        return INTENT_REFUND
    # 3. 查订单/物流
    if any(w in msg for w in ["订单", "物流", "快递", "发货", "到哪", "收货", "我的单"]):
        return INTENT_ORDER
    # 4. 求推荐
    if any(w in msg for w in ["推荐", "建议", "买什么", "买点", "猜你喜欢", "来点"]):
        return INTENT_RECOMMEND
    # 5. 查商品（库存/价格类提问，或消息里直接带商品名）
    if any(w in msg for w in ["库存", "有货", "没货", "多少钱", "价格", "便宜"]) \
            or _find_products(message, limit=1):
        return INTENT_PRODUCT
    # 6. 打招呼
    if any(w in msg for w in ["你好", "您好", "hi", "hello", "在吗", "你是"]):
        return INTENT_GREETING
    return INTENT_FALLBACK


def _product_line(p):
    if p.stock <= 0:
        stock_text = "目前已售罄，到货后会第一时间上架"
    else:
        stock_text = f"库存 {p.stock} 件"
    return f"【{p.name}】¥{p.price:.2f}，{stock_text}"


def chat(user, message):
    """智能客服主入口。
    user: 当前登录用户对象（未登录传 None）；message: 用户消息文本。
    返回 {"reply": str, "intent": str, "products": [Product], "need_login": bool}
    """
    intent = _detect_intent(message)

    if intent == INTENT_SAFETY:
        return {"reply": SAFETY_REPLY, "intent": intent, "products": []}

    if intent == INTENT_GREETING:
        return {"reply": GREETING_REPLY, "intent": intent, "products": []}

    if intent == INTENT_REFUND:
        return {"reply": REFUND_REPLY, "intent": intent, "products": []}

    if intent == INTENT_ORDER:
        # 水平越权防护：只能查自己的订单；未登录引导登录
        if not user:
            return {"reply": "查订单需要先登录哦，点右上角「登录」后再问我，我就能告诉你订单到哪了~",
                    "intent": intent, "products": [], "need_login": True}
        order = Order.query.filter_by(user_id=user.id)\
                           .order_by(Order.created_at.desc()).first()
        if not order:
            return {"reply": "你目前还没有订单，先去首页挑点喜欢的吧~",
                    "intent": intent, "products": []}
        reply = (f"你最近的订单 #{order.id}：金额 ¥{order.total_amount:.2f}，"
                 f"当前状态【{Order.STATUS.get(order.status, order.status)}】，"
                 f"下单时间 {order.created_at.strftime('%Y-%m-%d %H:%M')}。")
        if order.status == "pending":
            reply += " 还没付款哦，去「我的订单」点「去支付」就能完成下单~"
        elif order.status == "paid":
            reply += " 已付款，商家正在准备发货，请耐心等待~"
        elif order.status == "shipped":
            reply += " 已发货，注意查收，收到后记得点「确认收货」~"
        elif order.status == "completed":
            reply += " 已完成，感谢购买！"
        return {"reply": reply, "intent": intent, "products": []}

    if intent == INTENT_PRODUCT:
        products = _find_products(message)
        if not products:
            return {"reply": "你想查哪件商品呢？告诉我名字就行，比如「键盘还有货吗」「鼠标多少钱」~",
                    "intent": intent, "products": []}
        lines = "；".join(_product_line(p) for p in products)
        return {"reply": f"帮你查到了：{lines}。", "intent": intent, "products": products}

    if intent == INTENT_RECOMMEND:
        products = recommend_products(user, limit=3)
        if not products:
            return {"reply": "暂时没有可推荐的商品，稍后再来看看吧~",
                    "intent": intent, "products": []}
        names = "、".join(p.name for p in products)
        prefix = "根据你的购物偏好，" if user else "为你推荐热销好物，"
        return {"reply": f"{prefix}可以看看：{names}，点下方卡片可直达首页~",
                "intent": intent, "products": products}

    return {"reply": FALLBACK_REPLY, "intent": INTENT_FALLBACK, "products": []}


def recommend_products(user, limit=4):
    """猜你喜欢：
    - 未登录：按库存充足度推「热销好物」
    - 已登录：根据购物车 + 历史订单的品类推配套商品
      （买键盘 → 推鼠标/扩展坞/耳机），不重复推买过的商品，数量不足时补位
    """
    base = Product.query.filter(Product.stock > 0, Product.is_deleted == 0)

    if not user:
        return base.order_by(Product.stock.desc()).limit(limit).all()

    # 收集用户接触过的商品（购物车 + 历史订单明细）
    touched_ids = {c.product_id for c in Cart.query.filter_by(user_id=user.id).all()}
    for o in Order.query.filter_by(user_id=user.id).all():
        touched_ids.update(it.product_id for it in o.items)

    touched = Product.query.filter(Product.id.in_(touched_ids)).all() if touched_ids else []
    my_cats = {w for p in touched for w in CATEGORY_WORDS if w in p.name}

    # 配套品类（排除自己已经买过的品类）
    want_words = []
    for cat in my_cats:
        for w in COMPLEMENT.get(cat, []):
            if w not in my_cats and w not in want_words:
                want_words.append(w)

    result, seen = [], set(touched_ids)
    for w in want_words:
        for p in base.order_by(Product.stock.desc()).all():
            if p.id not in seen and w in p.name:
                result.append(p)
                seen.add(p.id)
                break  # 每个配套品类推一件
        if len(result) >= limit:
            break

    # 补位：其余有库存的商品
    for p in base.order_by(Product.stock.desc()).all():
        if len(result) >= limit:
            break
        if p.id not in seen:
            result.append(p)
            seen.add(p.id)

    return result[:limit]
