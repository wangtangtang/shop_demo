"""售后智能初审 —— 可解释规则引擎（Human-in-the-loop 人在回路）
================================================================
当前实现：纯 Python 关键词规则引擎，**离线可运行**（无网络、无 DB、无第三方依赖），
答辩现场断网也能完整演示。规则以常量列表维护（见下方“规则表”），每条命中都可解释，
输出统一结构：

    {
        "suggestion": "approve" | "reject" | "manual",  # 建议同意 / 建议拒绝 / 需人工核实
        "reason": "给管理员看的一句中文理由（含命中词，可解释）",
        "matched": ["实际命中的关键词...（去重）"],
    }

关键边界：本模块只产出“建议”，**绝不直接改售后单状态**。售后单状态（pending →
approved/rejected）的唯一变更入口是管理员后台的
POST /api/admin/aftersales/<id>/handle —— 最终裁定永远由人工完成（人在回路）。

后续扩展点：未来可把 analyze_aftersale 函数体替换为大模型调用（如云端 LLM 网关），
只要保持【入参 (aftersale_type, reason)、出参 dict 形状】不变，routes.py / models.py /
前端均无需改动。模型不可用时也可自动降级回本规则引擎（fallback）。

设计约束：
- analyze_aftersale 必须是纯函数：同样的输入永远得到同样的输出；
- 不 import flask / db，不发请求，不读数据库，方便单测、方便离线跑；
- reason 由服务端拼好返回，前端只负责展示，避免展示逻辑分散。
"""
import re

# ============ 规则表（论文/答辩里可直接列这张表） ============
# 1) 客观问题词：描述商品质量、物流履约等客观事实 → 命中倾向【建议同意】
OBJECTIVE_KEYWORDS = [
    "坏了", "破损", "碎了", "漏发", "少发", "缺件", "错发", "发错",
    "质量问题", "假货", "故障", "不能用", "无法使用", "开不了机",
    "变质", "空包", "没收到", "损坏", "瑕疵", "漏电", "过期",
]

# 2) 主观原因词：买家个人偏好/下单失误，非商家责任 → 命中倾向【建议拒绝】
SUBJECTIVE_KEYWORDS = [
    "不喜欢", "不想要", "拍错", "买错", "选错", "下错单", "后悔",
    "冲动消费", "朋友买的", "七天无理由", "无理由", "单纯不喜欢",
]

# 3) 价保特有词：仅当售后类型 type=price_protect（价保）时命中才算客观证据
#    （普通退货单里出现“便宜了”不构成价保理由，避免误判）
PRICE_PROTECT_KEYWORDS = ["降价", "买贵", "差价", "价保", "便宜了"]

# 建议值 → 中文短文案（后端展示层共用这一份映射，与 models 状态字典风格一致）
SUGGESTION_TEXT = {
    "approve": "建议同意",
    "reject": "建议拒绝",
    "manual": "需人工核实",
}

# 去标点/空白用：所有非中英文数字字符一律视为标点符号
_PUNCT_RE = re.compile(r"[^\u4e00-\u9fa5A-Za-z0-9]")


def _hit_keywords(text, words):
    """返回 text 中命中的关键词列表（按规则表顺序，去重保序）。"""
    hit = []
    for w in words:
        if w in text and w not in hit:
            hit.append(w)
    return hit


def analyze_aftersale(aftersale_type, reason):
    """对一条售后申请做智能初审，返回建议（纯函数，无副作用）。

    :param aftersale_type: 售后类型 refund_return / exchange / price_protect
    :param reason: 顾客填写的申请原因（调用方一般已 strip；本函数内部再兜底一次）
    :return: {"suggestion", "reason", "matched"}

    判定优先级：
      0. 信息过少（空 / 纯标点 / 去标点后长度 < 4）→ manual
      1. 客观词命中数 >= 1 且 >= 主观词命中数 → approve（客观证据优先）
      2. 只有主观词命中（客观 0、主观 >= 1）→ reject
      3. 两类都没命中，或两类都命中但主观更多 → manual
    """
    text = (reason or "").strip()

    # ---- 0. 信息过少：空串 / 纯标点 / 有效内容不足 4 个字 → 交人工 ----
    compact = _PUNCT_RE.sub("", text)
    if not compact or len(compact) < 4:
        return {
            "suggestion": "manual",
            "reason": "申请信息过少，需人工核实",
            "matched": [],
        }

    # ---- 统计两类词命中（关键词在原文中做子串匹配，保留顾客原话的可读性）----
    objective_hits = _hit_keywords(text, OBJECTIVE_KEYWORDS)
    subjective_hits = _hit_keywords(text, SUBJECTIVE_KEYWORDS)
    # 价保特有词：只有价保单才计入客观证据
    if aftersale_type == "price_protect":
        objective_hits += _hit_keywords(text, PRICE_PROTECT_KEYWORDS)

    n_obj, n_sub = len(objective_hits), len(subjective_hits)
    matched = objective_hits + [w for w in subjective_hits if w not in objective_hits]

    # ---- 1. 客观证据占优（含只有客观词、客观=主观打平）→ 建议同意 ----
    if n_obj >= 1 and n_obj >= n_sub:
        return {
            "suggestion": "approve",
            "reason": "申请描述包含客观质量/履约问题：%s，建议同意"
                      % "、".join(matched),
            "matched": matched,
        }

    # ---- 2. 只有主观原因（买家偏好/拍错类）→ 建议拒绝（最终仍由管理员裁定）----
    if n_sub >= 1 and n_obj == 0:
        return {
            "suggestion": "reject",
            "reason": "申请描述为买家主观原因：%s，建议拒绝（最终由管理员裁定）"
                      % "、".join(subjective_hits),
            "matched": matched,
        }

    # ---- 3. 都没命中 / 主观词更多 → 人工核实 ----
    if n_obj == 0 and n_sub == 0:
        return {
            "suggestion": "manual",
            "reason": "未命中自动审核规则，描述内容需人工核实",
            "matched": [],
        }
    # 两类都命中但主观词更多：证据冲突，交人工判断
    return {
        "suggestion": "manual",
        "reason": "申请同时包含客观问题与主观原因（%s），证据冲突需人工核实"
                  % "、".join(matched),
        "matched": matched,
    }
