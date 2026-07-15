from tests.test_brokers._base import BrokerTestBase
from zmqtt import Subscription


class BaseTestEMQX(BrokerTestBase):
    async def handle_sub_duplicates(
        self,
        *,
        sub: Subscription,
        n_duplicates: int,
    ) -> None:
        for _ in range(n_duplicates):
            await sub.get_message()
        assert sub._queue.empty()


class TestEMQXV311(BaseTestEMQX):
    host = "127.0.0.1"
    port = 1888
    version = "3.1.1"


class TestEMQXV5(BaseTestEMQX):
    host = "127.0.0.1"
    port = 1888
    version = "5.0"
