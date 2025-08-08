import os, sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from bnc_anc_pkg.decision import decide_trade_from_title


def test_decide_trade_long():
    decision, bases = decide_trade_from_title("Binance Will List Foo (FOO)")
    assert decision == "long"
    assert bases == ["FOO"]


def test_decide_trade_short():
    decision, bases = decide_trade_from_title("Binance will delist BAR on 2025-01-01")
    assert decision == "short"
    assert "BAR" in bases


def test_decide_trade_none():
    decision, bases = decide_trade_from_title("Some other news")
    assert decision == "none"
    assert bases == []
