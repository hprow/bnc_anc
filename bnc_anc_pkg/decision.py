import re
from typing import Set, Tuple, List

BTC_ALIAS = {"BTC": "XBT"}


def _bases_from_parentheses(title: str) -> Set[str]:
    out: Set[str] = set()
    for tok in re.findall(r"\(([A-Z0-9]{2,15})\)", title or ""):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", tok):
            continue
        if tok.upper() in {"USDT", "USDC", "USD", "FUTURES", "ALPHA"}:
            pass
        out.add(tok.upper())
    return out


def _bases_from_usdt_pairs(title: str) -> Set[str]:
    return {m.upper() for m in re.findall(r"\b([A-Z0-9]{2,30})USDT\b", title or "")}


def _bases_from_delist(title: str) -> Set[str]:
    m = re.search(r"delist\s+(.+)", title or "", flags=re.I)
    if not m:
        return set()
    seg = m.group(1)
    seg = seg.split(" on ")[0]
    parts = re.split(r"[,\s]+and\s+|,\s*|\s+", seg)
    out: Set[str] = set()
    for p in parts:
        tok = p.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,15}", tok):
            continue
        if tok in {"USDT", "USDC", "USD"}:
            continue
        out.add(tok)
    return out


def decide_trade_from_title(title: str) -> Tuple[str, List[str]]:
    if not title:
        return "none", []
    tl = title.lower()
    if "will delist" in tl or re.search(r"\bdelist\b", tl):
        bases = _bases_from_delist(title)
        return ("short", sorted(bases)) if bases else ("none", [])
    if any(k in tl for k in ("will add", "will list", "will be available", "will launch", "futures will launch")):
        bases = set()
        bases |= _bases_from_parentheses(title)
        bases |= _bases_from_usdt_pairs(title)
        return ("long", sorted(bases)) if bases else ("none", [])
    return "none", []
