from tests.test_brokers._base import BrokerTestBase
from zmqtt.client import Subscription


class BaseTestNanoMQ(BrokerTestBase):
    async def handle_sub_duplicates(
        self,
        *,
        sub: Subscription,
        n_duplicates: int,
    ) -> None:
        for _ in range(n_duplicates):
            await sub.get_message()
        assert sub._queue.empty()


class TestNanoMQV311(BaseTestNanoMQ):
    host = "127.0.0.1"
    port = 1887
    version = "3.1.1"


class TestNanoMQV5(BaseTestNanoMQ):
    host = "127.0.0.1"
    port = 1887
    version = "5.0"
