import os, sys, asyncio
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from bnc_anc_pkg.handler import handle_payload
from bnc_anc_pkg.exchanges.noop import NoOpExchange


class Recorder(NoOpExchange):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def trade(self, **kwargs):
        self.calls.append(kwargs)
        return await super().trade(**kwargs)


def test_handle_payload_multiple_exchanges():
    payload = {"title": "Binance Will List Foo (FOO) and Bar (BAR)", "catalogId": 48}
    ex1, ex2 = Recorder(), Recorder()
    asyncio.run(handle_payload(payload, [ex1, ex2]))
    assert len(ex1.calls) == 2
    assert len(ex2.calls) == 2
