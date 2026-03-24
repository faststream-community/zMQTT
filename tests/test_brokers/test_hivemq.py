from tests.test_brokers._base import BrokerTestBase
from zmqtt import Subscription


class BaseTestHiveMQ(BrokerTestBase):
    async def handle_sub_duplicates(
        self,
        *,
        sub: Subscription,
        n_duplicates: int,  # noqa: ARG002
    ) -> None:
        assert sub._queue.empty()


class TestHiveMQV311(BaseTestHiveMQ):
    host = "127.0.0.1"
    port = 1886
    version = "3.1.1"


class TestHiveMQV5(BaseTestHiveMQ):
    host = "127.0.0.1"
    port = 1886
    version = "5.0"
