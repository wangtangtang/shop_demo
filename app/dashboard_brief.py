"""AI 经营简报 —— 模板式 NLG（Template-based Natural Language Generation）
============================================================================
当前实现：纯 Python 模板文案生成，**离线可运行**（无网络、无 DB、无第三方依赖），
答辩现场断网也能出简报。routes 层负责取数组装 stats dict，本模块只做「数据 → 中文
简报」的转换，与 app/aftersale_ai.py（规则引擎纯函数）同一设计哲学：

    入参 stats（形状固定，缺项一律安全降级，绝不抛 KeyError）：
    {
        "days": 7,                        # 统计周期（7/30）
        "overview": {                     # /admin/stats/overview 的 data
            "gmv": 1280.0, "order_count": 5, "valid_order_count": 4,
            "avg_order_value": 320.0, "pay_rate": 80.0,
            "aftersale_rate": 5.0, "aftersale_count": 1,
            "avg_rating": 4.6, "rating_count": 3,
            "gmv_prev": 1000.0, "gmv_change_pct": 28.0,      # 上期为 0 时为 None
            "order_count_prev": 4, "order_count_change_pct": 25.0
        },
        "trend": [...],                   # /admin/stats/sales-trend 的 data（预留，当前不强制使用）
        "top_products": [                 # /admin/stats/top-products 的 data
            {"product_id": 1, "product_name": "机械键盘", "qty": 3, "amount": 897.0}
        ],
        "ai_panel": {                     # /admin/stats/ai/aftersale 的 data
            "total": 10, "analyzed": 10, "pending": 2,
            "suggestion_dist": {"approve": 6, "reject": 1, "manual": 3},
            "handled_total": 8, "agreed": 7, "decided_total": 7,
            "agreement_rate": 87.5        # 无可比样本时为 None
        }
    }

    出参（形状固定，前端直接渲染）：
    {
        "title": "近7天 AI 经营简报",
        "paragraphs": ["开场句（GMV/订单量/环比）", "热销句", "AI 初审句"],
        "alerts": [{"level": "good" | "warn" | "info", "text": "预警/表扬文案"}]
    }

安全边界：
- 任何数字为 0 / None、列表为空时都必须降级为通顺中文，输出里永远不出现
  "None" / "NaN" 字样；
- 纯函数：同样的 stats 永远得到同样的简报，不 import flask/db、不发请求；
- 环比方向阈值：|change_pct| <= 1% 记为「持平」，>1% 上升 / <-1% 下降。

后续扩展点：未来可把 generate_briefing 函数体替换为大模型调用（云端 LLM 网关），
只要保持【入参 stats dict、出参 dict 形状】不变，routes.py / 前端无需改动；
大模型不可用时降级回本模板实现（fallback），答辩断网场景永远可演示。
"""

# 环比方向阈值：变化率绝对值 <= 1% 视为持平
FLAT_THRESHOLD = 1.0
# 售后率预警线（>10% 预警）
AFTERSALE_WARN_RATE = 10.0
# GMV 环比下滑预警线（<-10% 预警）
GMV_DROP_WARN_PCT = -10.0
# 支付率预警线（<60% 提醒待支付积压）
PAY_RATE_WARN = 60.0
# AI 采纳率评级线
AI_RATE_GOOD = 80.0
AI_RATE_WARN = 60.0

ALERT_GOOD = "good"
ALERT_WARN = "warn"
ALERT_INFO = "info"


def _num(value, default=0.0):
    """安全转 float：None/非数字/NaN 一律降级为 default，杜绝输出 NaN。"""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    # float("nan") 不报错，这里必须显式拦掉
    if n != n or n in (float("inf"), float("-inf")):
        return default
    return n


def _money(value):
    """金额格式化为 ¥1,280.00 风格（千分位 + 两位小数）。"""
    return "\u00a5{:,.2f}".format(_num(value))


def _pct(value):
    """百分比格式化：28.0% / -15.5%。调用方需保证 value 非 None。"""
    return "{:.1f}%".format(_num(value))


def _direction(change_pct):
    """环比方向：None（无上期数据）→ None；±1% 内 → 持平；其余上升/下降。"""
    if change_pct is None:
        return None
    pct = _num(change_pct, 0.0)
    if pct > FLAT_THRESHOLD:
        return "上升"
    if pct < -FLAT_THRESHOLD:
        return "下降"
    return "持平"


def generate_briefing(stats):
    """根据看板统计数据生成 AI 经营简报（纯函数，无副作用）。

    :param stats: 见模块 docstring 的固定形状 dict（overview/trend/top_products/ai_panel）
    :return: {"title", "paragraphs": [...], "alerts": [{level, text}, ...]}
    """
    stats = stats or {}
    days = int(_num(stats.get("days"), 7)) or 7
    overview = stats.get("overview") or {}
    top_products = stats.get("top_products") or []
    ai_panel = stats.get("ai_panel") or {}

    paragraphs = []
    alerts = []

    gmv = _num(overview.get("gmv"))
    order_count = int(_num(overview.get("order_count")))
    gmv_change = overview.get("gmv_change_pct")
    if gmv_change is not None:
        gmv_change = _num(gmv_change, 0.0)
    pay_rate = overview.get("pay_rate")
    aftersale_rate = overview.get("aftersale_rate")

    # ---- 开场句：周期 GMV + 订单量 + 环比方向；零成交安全降级 ----
    if gmv <= 0 and order_count <= 0:
        paragraphs.append("近{}天暂无成交订单，GMV 为 {}。".format(days, _money(0)))
    else:
        parts = [
            "近{}天 GMV {}".format(days, _money(gmv)),
            "成交订单 {} 单".format(order_count),
        ]
        direction = _direction(gmv_change)
        if direction is None:
            parts.append("上一周期无成交，暂无环比数据")
        elif direction == "持平":
            parts.append("GMV 环比持平（{}）".format(_pct(gmv_change)))
        else:
            parts.append("GMV 环比{} {}".format(direction, _pct(abs(gmv_change))))
        paragraphs.append("，".join(parts) + "。")

    # ---- 热销句：TOP1 商品名 + 销量；无成交给明确空态 ----
    if top_products:
        top = top_products[0] or {}
        name = str(top.get("product_name") or "热销商品")
        qty = int(_num(top.get("qty")))
        amount = _money(top.get("amount"))
        paragraphs.append(
            "热销商品 TOP1 为「{}」，本周期售出 {} 件，销售额 {}。".format(name, qty, amount))
    else:
        paragraphs.append("本周期暂无成交，热销榜为空。")

    # ---- 预警 1：售后率偏高（仅在本周期有订单时判断）----
    if order_count > 0 and aftersale_rate is not None:
        rate = _num(aftersale_rate)
        if rate > AFTERSALE_WARN_RATE:
            alerts.append({
                "level": ALERT_WARN,
                "text": "售后率达 {}，超过 {}% 警戒线，请关注近期商品质量与物流履约问题。"
                        .format(_pct(rate), int(AFTERSALE_WARN_RATE)),
            })

    # ---- 预警 2：GMV 环比明显下滑 ----
    if gmv_change is not None and gmv_change < GMV_DROP_WARN_PCT:
        alerts.append({
            "level": ALERT_WARN,
            "text": "GMV 环比下降 {}，经营数据明显下滑，建议排查流量、价格或库存原因。"
                    .format(_pct(abs(gmv_change))),
        })

    # ---- 预警 3：支付率偏低，待支付积压 ----
    if order_count > 0 and pay_rate is not None:
        pr = _num(pay_rate)
        if pr < PAY_RATE_WARN:
            alerts.append({
                "level": ALERT_WARN,
                "text": "支付率仅 {}，低于 {}%，存在待支付订单积压，建议催付或检查支付链路。"
                        .format(_pct(pr), int(PAY_RATE_WARN)),
            })

    # ---- AI 句：初审采纳率 + 建议分布；无已处理样本时降级，不算好/坏 ----
    dist = ai_panel.get("suggestion_dist") or {}
    n_approve = int(_num(dist.get("approve")))
    n_reject = int(_num(dist.get("reject")))
    n_manual = int(_num(dist.get("manual")))
    handled_total = int(_num(ai_panel.get("handled_total")))
    agreement_rate = ai_panel.get("agreement_rate")
    if agreement_rate is not None:
        agreement_rate = _num(agreement_rate)

    dist_text = "建议分布：同意 {} / 拒绝 {} / 人工核实 {}".format(
        n_approve, n_reject, n_manual)
    if agreement_rate is None:
        paragraphs.append(
            "AI 售后初审：暂无已处理（同意/拒绝）且建议明确的工单，采纳率暂无数据；{}。"
            .format(dist_text))
        alerts.append({
            "level": ALERT_INFO,
            "text": "AI 初审暂无足够的已处理样本，采纳率待数据积累后计算。",
        })
    else:
        paragraphs.append(
            "AI 售后初审：已处理工单 {} 单，建议与人工裁定一致率 {}；{}。".format(
                handled_total, _pct(agreement_rate), dist_text))
        if agreement_rate >= AI_RATE_GOOD:
            alerts.append({
                "level": ALERT_GOOD,
                "text": "AI 初审采纳率达 {}，规则建议与人工裁定高度一致。"
                        .format(_pct(agreement_rate)),
            })
        elif agreement_rate < AI_RATE_WARN:
            alerts.append({
                "level": ALERT_WARN,
                "text": "AI 初审采纳率仅 {}，低于 {}%，建议复核规则库关键词是否需要调优。"
                        .format(_pct(agreement_rate), int(AI_RATE_WARN)),
            })
        else:
            alerts.append({
                "level": ALERT_INFO,
                "text": "AI 初审采纳率 {}，处于观察区间，可持续跟踪规则命中情况。"
                        .format(_pct(agreement_rate)),
            })

    return {
        "title": "近{}天 AI 经营简报".format(days),
        "paragraphs": paragraphs,
        "alerts": alerts,
    }
