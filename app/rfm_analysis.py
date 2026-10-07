"""用户 RFM 分层 —— 确定性打分规则（看板二期）
================================================================
当前实现：纯 Python 打分分层，**离线可运行**（无网络、无 DB、无第三方依赖），
与 app/aftersale_ai.py / app/dashboard_brief.py / app/review_ai.py 同一设计
哲学。routes 层负责从窗口内有效订单聚合出 per-user 的原始值，本模块只做
「原始 R/F/M → 中位数打分 → 8 类经典分层」的转换。

入参 build_rfm：
    users : [
        {"user_id": 1, "username": "张三",
         "r_days": 2,       # Recency：窗口基准日 - 该用户最近一笔有效订单的天数（整数，越小越好）
         "f": 3,            # Frequency：窗口内有效订单数
         "m": 896.5},       # Monetary：窗口内有效订单 total_amount 合计
        ...
    ]
    days : int              # 统计窗口天数（仅原样回显到 summary，不参与计算）

打分口径（确定性、可解释、可单测）：
- 三维度分别与【全体用户中位数】比较：
    R：r_days <= 中位数记 2，> 中位数记 1（越近越好）
    F：f >= 中位数记 2，< 中位数记 1
    M：m >= 中位数记 2，< 中位数记 1
- 样本只有 1 个用户时：该用户各维度恰好等于中位数，稳定记 2（212 不崩）。

出参 build_rfm（形状固定）：
    {
        "segments": {
            "<rfm码>": {"label": "重要价值客户", "count": 2,
                        "users": [{"user_id", "username", "r_days", "f", "m", "rfm": "222"}]},
            ...  # 8 层全部存在，空层 users=[] / count=0
        },
        "summary": {"total_users": 0, "days": 30,
                    "segments": {"重要价值客户": 0, ...}},   # 层名(中文)→人数
        "median": {"recency": 0.0, "frequency": 0.0, "monetary": 0.0},
    }

安全边界：
- 空集合 / None：segments 8 层结构仍完整，中位数返回 0（不是 None，前端好展示），
  除 0 不报错；任何输出不出现 inf / NaN；
- users 内项非 dict / 缺字段：该项跳过（总数也不计）；m 非数字按 0 处理；
- 每层用户明细按 r_days 升序、f 降序、m 降序、user_id 升序排列，结果确定。

后续扩展点：未来可把中位数打分替换为大模型/聚类（如 K-Means 自动找切点），
只要保持【入参/出参 dict 形状】不变，routes.py / 前端无需改动；模型不可用时
降级回本确定性实现（fallback）。
"""

# 8 类经典分层：RFM 分值码 → 中文名（论文里可直接引用这张映射表）
SEGMENT_LABELS = {
    "222": "重要价值客户",
    "212": "重要发展客户",
    "122": "重要保持客户",
    "112": "重要挽留客户",
    "221": "一般价值客户",
    "211": "一般发展客户",
    "121": "一般保持客户",
    "111": "一般挽留客户",
}

# segments 出参固定顺序（经典分层展示顺序，字典按此顺序构造）
SEGMENT_ORDER = ["222", "212", "122", "112", "221", "211", "121", "111"]


def _safe_num(value):
    """安全转 float：None/非数字/NaN/inf 一律按 0 处理，杜绝输出 NaN/inf。"""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 0.0
    if n != n or n in (float("inf"), float("-inf")):
        return 0.0
    return n


def median(values):
    """中位数（偶数个取中间两数均值）；空列表返回 0。输入应为已清洗数值。"""
    n = len(values)
    if n == 0:
        return 0
    s = sorted(values)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _clean_users(users):
    """清洗入参：跳过非 dict/缺 user_id 的项，规整字段类型，返回干净列表。"""
    cleaned = []
    if not isinstance(users, list):
        return cleaned
    for raw in users:
        if not isinstance(raw, dict) or raw.get("user_id") is None:
            continue
        # r_days 兜底为非负整数（None 视为窗口基准当天也不合理——调用方保证有值，
        # 这里仅防御；负数也按 0 处理）
        r_days = int(_safe_num(raw.get("r_days")))
        if r_days < 0:
            r_days = 0
        freq = int(_safe_num(raw.get("f")))
        if freq < 0:
            freq = 0
        cleaned.append({
            "user_id": raw.get("user_id"),
            "username": str(raw.get("username") or ""),
            "r_days": r_days,
            "f": freq,
            "m": round(_safe_num(raw.get("m")), 2),
        })
    return cleaned


def build_rfm(users, days=30):
    """对窗口内用户做 RFM 中位数打分与 8 类分层（纯函数，无副作用）。

    :param users: 见模块 docstring 的 per-user 原始值列表
    :param days: 统计窗口天数（仅回显）
    :return: {"segments", "summary", "median"}，空集合结构仍完整
    """
    rows = _clean_users(users)

    med_r = median([r["r_days"] for r in rows])
    med_f = median([r["f"] for r in rows])
    med_m = median([r["m"] for r in rows])

    segments = {code: {"label": SEGMENT_LABELS[code], "count": 0, "users": []}
                for code in SEGMENT_ORDER}

    for r in rows:
        # R：越近越好，<= 中位数记 2；F/M：>= 中位数记 2
        rs = 2 if r["r_days"] <= med_r else 1
        fs = 2 if r["f"] >= med_f else 1
        ms = 2 if r["m"] >= med_m else 1
        code = "%d%d%d" % (rs, fs, ms)
        user_row = {
            "user_id": r["user_id"],
            "username": r["username"],
            "r_days": r["r_days"],
            "f": r["f"],
            "m": r["m"],
            "rfm": code,
        }
        segments[code]["users"].append(user_row)

    for code in SEGMENT_ORDER:
        seg = segments[code]
        # 明细排序确定：先看最近购买，再看频次/金额
        seg["users"].sort(key=lambda u: (u["r_days"], -u["f"], -u["m"], u["user_id"]))
        seg["count"] = len(seg["users"])

    summary_segments = {SEGMENT_LABELS[code]: segments[code]["count"]
                        for code in SEGMENT_ORDER}
    return {
        "segments": segments,
        "summary": {
            "total_users": len(rows),
            "days": days,
            "segments": summary_segments,
        },
        "median": {
            "recency": round(_safe_num(med_r), 1),
            "frequency": round(_safe_num(med_f), 1),
            "monetary": round(_safe_num(med_m), 2),
        },
    }
