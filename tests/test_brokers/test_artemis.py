from tests.test_brokers._base import BrokerTestBase
from zmqtt import Subscription


class BaseTestArtemis(BrokerTestBase):
    async def handle_sub_duplicates(
        self,
        *,
        sub: Subscription,
        n_duplicates: int,
    ) -> None:
        for _ in range(n_duplicates):
            await sub.get_message()
        assert sub._queue.empty()


class TestArtemisV311(BaseTestArtemis):
    host = "127.0.0.1"
    port = 1883
    version = "3.1.1"


class TestArtemisV5(BaseTestArtemis):
    host = "127.0.0.1"
    port = 1883
    version = "5.0"
