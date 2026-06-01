from __future__ import annotations

import re

from .models import Article


class LocalBriefClient:
    """文首一段总括 + 四维分节（无 LLM）；总括无小标题，不输出正文全文。"""

    #: 启发式抽取无法保证四维均有实质要点；流水线默认跳过「四维门禁」以免清空输出。
    skip_four_dimension_validation: bool = True

    def __init__(self, max_chars: int = 12000) -> None:
        #: 用于从正文抽取句子的上限（内部处理），不在输出中展示长段原文。
        self.max_chars = max_chars

    def summarize_article(self, article: Article, content: str) -> str:
        raw = _compose_analysis_text(article, content, max_chars=self.max_chars)

        fact_u, bg_u, impact_u, contra_u = _split_dimensions(raw)

        lines = [
            _compose_points_summary(article, fact_u, bg_u, impact_u, contra_u),
            "",
            "#### 事实",
            _units_to_paragraph(
                title=article.title,
                section="事实",
                units=fact_u,
                backup_units=[],
            ),
            "",
            "#### 背景",
            _units_to_paragraph(
                title=article.title,
                section="背景",
                units=bg_u,
                backup_units=[],
            ),
            "",
            "#### 产生的影响",
            _units_to_paragraph(
                title=article.title,
                section="产生的影响",
                units=impact_u,
                backup_units=[],
            ),
            "",
            "#### 反面观点 / 数据矛盾点",
            _units_to_paragraph(
                title=article.title,
                section="反面观点 / 数据矛盾点",
                units=contra_u,
                backup_units=fact_u + bg_u + impact_u,
            ),
        ]
        return "\n".join(lines)

    def one_sentence_from_brief(self, article: Article, brief_md: str) -> str | None:
        """本地抽取模式不调用大模型；一句话概要留空。"""
        return None


def _trim_unit(text: str, max_len: int = 130) -> str:
    t = " ".join(text.split())
    if len(t) <= max_len:
        return t
    return t[:max_len].rstrip("，、；;。 ")


def _compose_points_summary(
    article: Article,
    fact_u: list[str],
    bg_u: list[str],
    impact_u: list[str],
    contra_u: list[str],
) -> str:
    """仅复述正文证据句，不做推断或扩展。"""
    evidences: list[str] = []
    for group in (fact_u, bg_u, impact_u, contra_u):
        if group:
            evidences.append(_trim_unit(group[0], 120).rstrip("。"))
    if evidences:
        return "；".join(evidences[:3]) + "。"
    if article.title:
        return f"标题：{_trim_unit(article.title, 80).rstrip('。')}。"
    return "正文证据不足，未形成可直接复述的总括。"


def _units_to_paragraph(
    *,
    title: str,
    section: str,
    units: list[str],
    backup_units: list[str],
    min_chars: int = 0,
    max_chars: int = 500,
) -> str:
    if section.startswith("反面观点"):
        explicit = _build_contra_pairs(units)
        if explicit:
            return _as_bullet(_fit_paragraph_length("；".join(explicit) + "。", max_chars=max_chars))
    if section.startswith("反面观点") and not units:
        candidates = _extract_verifiable_risk_points(backup_units)
        if candidates:
            txt = "；".join(candidates) + "。"
            return _as_bullet(_fit_paragraph_length(txt, max_chars=max_chars))
        return _as_bullet(_fit_paragraph_length("正文未出现可核查的矛盾口径或风险点证据句。", max_chars=max_chars))

    picked: list[str] = []
    used: set[str] = set()
    for pool in (units, backup_units):
        for u in pool:
            c = _normalize_sentence(u)
            if not c or c in used or _is_low_quality_sentence(c):
                continue
            picked.append(c)
            used.add(c)
            if len("".join(picked)) >= 220:
                break
        if len("".join(picked)) >= 220:
            break

    if not picked:
        txt = f"正文未提供可直接归入「{section}」的明确证据句，本节不补充、不扩展。"
        return _as_bullet(_fit_paragraph_length(txt, max_chars=max_chars))

    body = "；".join(picked)
    text = body + "。"
    return _as_bullet(_fit_paragraph_length(text, max_chars=max_chars))


_CONTRA_HINTS = (
    "然而",
    "但是",
    "不过",
    "相反",
    "质疑",
    "争议",
    "矛盾",
    "不一致",
    "未能证实",
    "尚未",
    "否认",
    "反驳",
    "质询",
    "口径",
    "说法不一",
    "前后不一",
    "相互矛盾",
    "版本不一致",
    "数据冲突",
    "存在分歧",
    "各执一词",
    "未获证实",
    "However",
    "but ",
    "deny",
    "dispute",
)
_IMPACT_HINTS = (
    "影响",
    "导致",
    "或将",
    "可能",
    "风险",
    "下跌",
    "上涨",
    "飙升",
    "暴跌",
    "加息",
    "降息",
    "股市",
    "油价",
    "金价",
    "市场",
    "投资者",
    "推动",
    "冲击",
    "威胁",
    "受益于",
)
_BG_HINTS = (
    "背景",
    "此前",
    "长期以来",
    "源于",
    "始于",
    "自",
    "历史",
    "早在",
    "谈判",
    "协议",
    "冲突",
)


def _split_dimensions(text: str) -> tuple[list[str], list[str], list[str], list[str]]:
    units = _split_units(text)
    if not units:
        return [], [], [], []

    fact_s: list[str] = []
    bg_s: list[str] = []
    impact_s: list[str] = []
    contra_s: list[str] = []

    for u in units:
        lower = u.casefold()
        if any(h in u for h in _CONTRA_HINTS) or any(h in lower for h in (" however", " but ")):
            contra_s.append(u)
        elif any(h in u for h in _IMPACT_HINTS):
            impact_s.append(u)
        elif any(h in u for h in _BG_HINTS):
            bg_s.append(u)
        else:
            fact_s.append(u)

    if not fact_s and units:
        fact_s = units[:]

    return fact_s, bg_s, impact_s, contra_s


def _split_units(text: str) -> list[str]:
    chunks = re.split(r"(?<=[。！？])\s+|\n+", text)
    out: list[str] = []
    for c in chunks:
        c = c.strip()
        if c and not _is_noise_unit(c):
            out.append(c)
    return out


def _normalize_sentence(value: str) -> str:
    t = " ".join(value.split()).strip("；;。 ")
    t = t.replace("……", "。").replace("...", "。").replace("…", "。")
    t = re.sub(r"\.{2,}", "。", t)
    t = re.sub(r"。{2,}", "。", t)
    t = re.sub(r"[：:]\s*第[一二三四五六七八九十]\s*$", "", t)
    t = re.sub(r"^\s*第[一二三四五六七八九十]\s*[，、:]?", "", t)
    t = t.strip("；;。 ")
    if not t:
        return ""
    return _trim_unit(t, max_len=120).rstrip("。")


def _fit_paragraph_length(text: str, *, max_chars: int) -> str:
    s = " ".join(text.split())
    if len(s) > max_chars:
        s = s[: max_chars - 1].rstrip("，、；;。 ") + "。"
    return s


def _as_bullet(text: str) -> str:
    clean = text.strip()
    return clean if clean.startswith("- ") else f"- {clean}"


def _compose_analysis_text(article: Article, content: str, *, max_chars: int) -> str:
    description = (article.description or "").strip()
    body = (content or "").strip()
    if description and description not in body:
        merged = f"{description}\n{body}".strip()
    else:
        merged = body or description or (article.title or "").strip()
    return merged[:max_chars]


def _is_noise_unit(value: str) -> bool:
    s = " ".join(value.split())
    if len(s) < 6:
        return True
    noise_tokens = (
        "登录",
        "注册",
        "扫一扫",
        "分享",
        "订阅",
        "广告",
        "Copyright",
        "All Rights Reserved",
        "点击",
        "下载APP",
    )
    return any(t in s for t in noise_tokens)


def _is_low_quality_sentence(value: str) -> bool:
    s = value.strip()
    if len(s) < 12:
        return True
    # Avoid heading-like fragments and obvious broken tails.
    bad_patterns = (
        r"^Source[:：]",
        r"^第[一二三四五六七八九十]$",
        r"[：:]\s*第[一二三四五六七八九十]\s*$",
        r"^(所有|此外|另外|但是|然而)[，、]?$",
    )
    return any(re.search(p, s) for p in bad_patterns)


def _extract_verifiable_risk_points(units: list[str]) -> list[str]:
    """When explicit contradictions are absent, derive evidence-based check points."""
    out: list[str] = []
    seen: set[str] = set()
    signal_words = (
        "达到",
        "提升",
        "下降",
        "减少",
        "增长",
        "第一",
        "领先",
        "仅",
        "高达",
        "%",
        "倍",
        "亿美元",
        "万人",
        "测试",
        "评分",
        "排名",
        "发布",
        "宣布",
    )
    for u in units:
        c = _normalize_sentence(u)
        if not c or c in seen or _is_low_quality_sentence(c):
            continue
        if any(w in c for w in signal_words) or re.search(r"\d", c):
            out.append(f"可核查争议点：正文给出的单一口径为「{c}」，缺少对照口径或来源交叉验证")
            seen.add(c)
        if len(out) >= 2:
            break
    return out


def _build_contra_pairs(units: list[str]) -> list[str]:
    """Convert contradiction-like sentences into explicit A-vs-B structure."""
    out: list[str] = []
    for u in units:
        s = _normalize_sentence(u)
        if not s or _is_low_quality_sentence(s):
            continue
        pair = _split_on_contrast_marker(s)
        if pair is None:
            continue
        left, right = pair
        left = left.strip("，、；; ")
        right = right.strip("，、；; ")
        if len(left) < 6 or len(right) < 6:
            continue
        out.append(f"反面对照：一方表述为「{left}」，但同段又出现「{right}」")
        if len(out) >= 2:
            break
    return out


def _split_on_contrast_marker(text: str) -> tuple[str, str] | None:
    markers = ("然而", "但是", "不过", "却", "但", "相反")
    for m in markers:
        idx = text.find(m)
        if idx > 0 and idx < len(text) - len(m):
            return text[:idx], text[idx + len(m) :]
    return None
