import asyncio

import pytest

from tests.test_brokers._base import BrokerTestBase
from zmqtt import Subscription


class BaseTestNanoMQ(BrokerTestBase):
    async def handle_sub_duplicates(
        self,
        *,
        sub: Subscription,
        n_duplicates: int,
    ) -> None:
        for _ in range(n_duplicates):
            await sub.get_message()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(sub.get_message(), timeout=0.2)


class TestNanoMQV311(BaseTestNanoMQ):
    host = "127.0.0.1"
    port = 1887
    version = "3.1.1"


class TestNanoMQV5(BaseTestNanoMQ):
    host = "127.0.0.1"
    port = 1887
    version = "5.0"
