"""Remove common news-site chrome / newsletter / translation boilerplate from extracted article text."""

from __future__ import annotations

import re


# Whole lines that are almost never article body (case-insensitive match after strip).
_LINE_EXACT_BLOCKLIST: frozenset[str] = frozenset(
    {
        "share",
        "see also",
        "related stories",
        "related articles",
        "more from",
        "subscribe",
        "newsletter",
        "sign up",
        "sign in",
        "log in",
        "follow us",
        "copy link",
        "打印",
        "收听",
        "复制链接",
        "分享到",
    }
)


# Lines matching these regexes are dropped entirely.
_LINE_REGEX_DROP: tuple[re.Pattern[str], ...] = (
    re.compile(r"^see\s+also\b", re.I),
    re.compile(r"^more\s+from\b", re.I),
    re.compile(r"^related\s+(articles|stories)\b", re.I),
    re.compile(r"^subscribe\b", re.I),
    re.compile(r"^newsletter\b", re.I),
    re.compile(r"^get\s+the\s+insights\b", re.I),
    re.compile(r"^delivered\s+to\s+your\s+inbox\b", re.I),
    re.compile(r"insights\s+delivered\s+to\s+your\s+inbox", re.I),
    re.compile(r"本文由\s*AI\s*辅助翻译"),
    re.compile(r"本文(?:由|为)?\s*AI\s*辅助"),
    re.compile(r"AI\s*辅助翻译"),
    re.compile(r"机器翻译"),
    re.compile(r"自动翻译"),
    re.compile(r"Error\s+loading\s+media", re.I),
    re.compile(r"Copy\s+Pause\s+Play", re.I),
    re.compile(r"Pause\s+Play\s+Next", re.I),
    re.compile(r"%\s*Buffered", re.I),
    re.compile(r"unstick\s+Share\s+this\s+video", re.I),
    re.compile(r"Share\s+this\s+video", re.I),
    re.compile(r"^\d+\s*%\s*Buffered", re.I),
    re.compile(r"Previous\s+Pause\s+Play\s+Next", re.I),
)

# Substrings removed everywhere (non-greedy fragments embedded in noisy sentences).
_INLINE_REMOVE: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s*Share\s+this\s+video[^。.]*", re.I),
    re.compile(r"\s*Error\s+loading\s+media[^。.?!]*", re.I),
    re.compile(r"\s*Copy\s+Pause\s+Play[^。.?!]*", re.I),
    re.compile(r"\s*%?\s*Buffered\s+[\d.]+\s*", re.I),
    re.compile(r"\s*Leonardo\s+Di\s+Caprio\s+unstick\s*", re.I),
)

_WHITESPACE_COLLAPSE = re.compile(r"[ \t]{2,}")
_NEWLINE_COLLAPSE = re.compile(r"\n{3,}")


def _drop_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    low = s.casefold()
    if low in _LINE_EXACT_BLOCKLIST:
        return True
    if len(s) <= 80:
        for rx in _LINE_REGEX_DROP:
            if rx.search(s):
                return True
    else:
        # Long lines: only drop if strongly boilerplate (translation tag lines can be long)
        for rx in _LINE_REGEX_DROP:
            if rx.search(s) and len(s) < 160:
                return True
    return False


def scrub_extracted_noise(text: str) -> str:
    """Filter boilerplate lines and inline chrome from OCR/HTML extraction artifacts."""
    if not text or not text.strip():
        return text

    for rx in _INLINE_REMOVE:
        text = rx.sub(" ", text)

    lines: list[str] = []
    for line in text.splitlines():
        if _drop_line(line):
            continue
        lines.append(line)

    out = "\n".join(lines)
    out = _WHITESPACE_COLLAPSE.sub(" ", out)
    out = _NEWLINE_COLLAPSE.sub("\n\n", out)
    return out.strip()
