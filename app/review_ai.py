"""评价标签洞察 —— 可解释关键词规则引擎（评论 AI 模块，看板二期）
================================================================
当前实现：纯 Python 关键词规则引擎，**离线可运行**（无网络、无 DB、无第三方
依赖），与 app/aftersale_ai.py（售后初审规则引擎）、app/dashboard_brief.py
（模板式 NLG）同一设计哲学。routes 层负责从库里取评价数据，本模块只做
「评论文本 + 评分 → 标签 / 情感倾向」与「批量聚合」两件事。

单条评价入参：
    content : str   评价原文（调用方一般已 strip；本函数内部再兜底一次）
    rating  : int   评分 1-5（可选；仅在文本没有任何标签命中时决定情感倾向）

单条评价出参 extract_tags：
    {
        "tags": ["质量好", "物流快", ...],   # 命中的标签（电商化标签名，去重）
        "sentiment": "positive" | "negative" | "neutral",
        "matched": ["质量好", ...],           # 实际命中的词典原词（去重，便于解释）
    }

情感判定口径（文本与评分冲突时以文本命中为主，可解释）：
    1) 命中负向标签数 > 正向 → negative；命中正向 > 负向 → positive
    2) 两类打平（含同时命中数量相等）：文本非空但标签打平 → 用评分兜底
       （rating 4-5 positive、1-2 negative、3/None neutral）
    3) 一条标签都没命中：同样用评分兜底；评分也没有 → neutral
    4) 空内容 / 纯标点 → tags=[]、matched=[]、sentiment=neutral（绝不给默认标签）

批量入参 aggregate_review_tags：
    entries : [{"content": str, "rating": int}, ...]   # 形状缺项安全降级

批量出参（形状固定，前端直接渲染）：
    {
        "total": 0,                # 评价总数
        "tagged": 0,               # 至少命中一个标签的评价数
        "tagged_rate": 0.0,        # 带标签占比（百分比，1 位小数；total=0 给 0）
        "sentiment_dist": {"positive": 0, "negative": 0, "neutral": 0},
        "positive": [{"tag": "质量好", "count": 3}, ...],  # 频次降序 TOP
        "negative": [{"tag": "破损", "count": 2}, ...],
    }

标签匹配规则（解决「满意/不满意」「发货快」与词表重叠的歧义）：
- 先在原文中找出全部命中片段（按起始位置升序、长度降序），再剔除被更长命中
  片段【严格包含】的短片段；因此「不满意」整段命中「不满意」标签，而不会
  再从同一位置抽出「满意」；而「物流很快，发货很快」这类并列表述能分别
  命中「物流快」「发货快」两个标签。
- 一条评价可命中多个标签；标签/命中词都去重保序。

后续扩展点：未来可把 extract_tags / aggregate_review_tags 的函数体整体替换
为大模型调用（如云端 LLM 网关做方面级情感分析 ABSA），只要保持【入参/出参
dict 形状】不变，routes.py / 前端无需改动；模型不可用时降级回本规则引擎
（fallback），答辩断网场景永远可演示。
"""
import re

# ============ 标签词典表（论文/答辩里可直接列这张表） ============
# 正向标签：词典原词 → 电商化标签名（一个标签可配多个同义词）
POSITIVE_TAG_WORDS = [
    ("物流快", ["物流很快", "快递很快", "送货很快", "配送很快", "物流快"]),
    ("质量好", ["质量很好", "质量不错", "做工不错", "质量好"]),
    ("包装严实", ["包装严实", "包装不错", "包装很好", "包装用心"]),
    ("性价比高", ["性价比很高", "性价比高", "物超所值", "划算"]),
    ("客服态度好", ["客服态度很好", "客服很好", "服务态度很好", "客服态度好"]),
    ("外观好看", ["外观好看", "颜值很高", "外观漂亮", "好看"]),
    ("发货快", ["发货很快", "发货速度快", "发货快"]),
    ("满意", ["非常满意", "很满意", "满意"]),
]

# 负向标签：词典原词 → 电商化标签名
NEGATIVE_TAG_WORDS = [
    ("破损", ["破损", "摔碎", "碎了", "损坏"]),
    ("漏发", ["漏发"]),
    ("少发", ["少发", "缺件", "少件"]),
    ("质量差", ["质量很差", "质量差", "做工差"]),
    ("色差", ["色差"]),
    ("物流慢", ["物流很慢", "快递很慢", "送货很慢", "物流慢", "太慢了"]),
    ("假货", ["假货", "假冒"]),
    ("瑕疵", ["瑕疵", "有缺陷"]),
    ("客服差", ["客服很差", "客服态度差", "客服差", "态度恶劣"]),
    ("不满意", ["不满意", "很失望"]),
]

# 聚合时每类标签返回的 TOP 数（前端横条够用；不做参数）
TAG_TOP_LIMIT = 10

# 情感常量（出参固定字符串，禁止散落魔法值）
SENTIMENT_POSITIVE = "positive"
SENTIMENT_NEGATIVE = "negative"
SENTIMENT_NEUTRAL = "neutral"

# 去标点用：所有非中英文数字字符一律视为标点符号
_PUNCT_RE = re.compile(r"[^\u4e00-\u9fa5A-Za-z0-9]")


def _find_hits(text, tag_words):
    """在 text 中找出标签词典的全部命中片段，返回 [(start, word_len, word, tag)]。
    同一原词重复出现只保留首次（命中词去重）。"""
    found = []
    seen = set()
    for tag, words in tag_words:
        for word in words:
            if not word or word in seen:
                continue
            idx = text.find(word)
            if idx >= 0:
                found.append((idx, len(word), word, tag))
                seen.add(word)
    return found


def _drop_contained(hits):
    """剔除被更长命中片段【严格包含】的短片段，返回保留的 hit 列表（保序）。
    判断基于 (start, word) 片段，语义上解决「不满意」包含「满意」的歧义；
    起始相同、长度相同（不同原词）的两个片段互不包含。"""
    kept = []
    for hit in hits:
        s, ln = hit[0], hit[1]
        contained = False
        for other in hits:
            os_, ol_ = other[0], other[1]
            if other is hit or ol_ <= ln:
                continue
            # other 片段严格覆盖当前片段
            if os_ <= s and os_ + ol_ >= s + ln and (os_ < s or os_ + ol_ > s + ln):
                contained = True
                break
        if not contained:
            kept.append(hit)
    return kept


def _rating_sentiment(rating):
    """评分兜底倾向：4-5 positive、1-2 negative、其余（3/None/非数字）neutral。"""
    try:
        r = int(rating)
    except (TypeError, ValueError):
        return SENTIMENT_NEUTRAL
    if r >= 4:
        return SENTIMENT_POSITIVE
    if r <= 2:
        return SENTIMENT_NEGATIVE
    return SENTIMENT_NEUTRAL


def extract_tags(content, rating=None):
    """从一条评价文本提取标签与情感倾向（纯函数，无副作用）。

    :param content: 评价原文（str；非 str 安全按空串处理）
    :param rating: 评分 1-5（可选，仅在标签无法决定情感时兜底）
    :return: {"tags": [...], "sentiment": ..., "matched": [...]}，详见模块 docstring
    """
    text = content.strip() if isinstance(content, str) else ""

    # ---- 空内容 / 纯标点：空标签，中性，绝不脑补 ----
    if not _PUNCT_RE.sub("", text):
        return {"tags": [], "sentiment": SENTIMENT_NEUTRAL, "matched": []}

    # 关键：正负两侧的命中片段必须【合并后统一做包含剔除】，否则跨极性的
    # 包含关系会漏判（「不满意」是负向标签、其中「满意」是正向标签，分组
    # 各自剔除时二者都会存活）。hit 第 5 位标注极性，剔除后再据此计数。
    raw_pos = _find_hits(text, POSITIVE_TAG_WORDS)
    raw_neg = _find_hits(text, NEGATIVE_TAG_WORDS)
    raw_hits = [h + (SENTIMENT_POSITIVE,) for h in raw_pos]
    raw_hits += [h + (SENTIMENT_NEGATIVE,) for h in raw_neg]
    kept_hits = _drop_contained(raw_hits)
    pos_hits = [h for h in kept_hits if h[4] == SENTIMENT_POSITIVE]
    neg_hits = [h for h in kept_hits if h[4] == SENTIMENT_NEGATIVE]

    # 按原文出现位置排序输出（一条评价多标签时阅读顺序自然），标签/原词分别去重
    ordered = sorted(kept_hits, key=lambda h: (h[0], -h[1]))
    tags, matched = [], []
    for s, ln, word, tag, polarity in ordered:
        if tag not in tags:
            tags.append(tag)
        if word not in matched:
            matched.append(word)

    n_pos, n_neg = len(pos_hits), len(neg_hits)
    if n_pos > n_neg:
        sentiment = SENTIMENT_POSITIVE
    elif n_neg > n_pos:
        sentiment = SENTIMENT_NEGATIVE
    else:
        # 标签打平（含两类都为 0）：评分兜底；文本与评分冲突时以文本命中为主——
        # 走到这里说明文本两侧势均力敌，评分给出最终解释
        sentiment = _rating_sentiment(rating)

    return {"tags": tags, "sentiment": sentiment, "matched": matched}


def aggregate_review_tags(entries):
    """批量聚合评价标签（纯函数，无副作用）。

    :param entries: [{"content": str, "rating": int}, ...]；非 list / 项非 dict
                    一律安全跳过，绝不抛异常
    :return: 固定形状 dict（total/tagged/tagged_rate/sentiment_dist/positive/negative）
    """
    pos_counts = {}
    neg_counts = {}
    dist = {SENTIMENT_POSITIVE: 0, SENTIMENT_NEGATIVE: 0, SENTIMENT_NEUTRAL: 0}
    total = 0
    tagged = 0

    if isinstance(entries, list):
        pos_names = {name for name, _ in POSITIVE_TAG_WORDS}
        neg_names = {name for name, _ in NEGATIVE_TAG_WORDS}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            total += 1
            res = extract_tags(entry.get("content"), entry.get("rating"))
            dist[res["sentiment"]] = dist.get(res["sentiment"], 0) + 1
            tag_set = set(res["tags"])
            if tag_set:
                tagged += 1
            # 每个标签按词典表归属到正/负一侧（标签名两侧不重名，避免歧义）
            for tag in tag_set:
                if tag in pos_names:
                    pos_counts[tag] = pos_counts.get(tag, 0) + 1
                elif tag in neg_names:
                    neg_counts[tag] = neg_counts.get(tag, 0) + 1

    positive = sorted(pos_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    negative = sorted(neg_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    tagged_rate = round(tagged * 100.0 / total, 1) if total else 0.0

    return {
        "total": total,
        "tagged": tagged,
        "tagged_rate": tagged_rate,
        "sentiment_dist": dist,
        "positive": [{"tag": t, "count": c} for t, c in positive[:TAG_TOP_LIMIT]],
        "negative": [{"tag": t, "count": c} for t, c in negative[:TAG_TOP_LIMIT]],
    }
