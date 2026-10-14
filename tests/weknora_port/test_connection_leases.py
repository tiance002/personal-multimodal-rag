import asyncio
import pytest
from backend.weknora_port.storage.connections import IsolatedConnectionFactory
from backend.weknora_port.storage.postgres import IsolationPolicy
from tests.weknora_port.test_migrate import MigrationConnection


def test_every_parallel_lease_has_dedicated_verified_connection_and_releases_guard():
    async def run():
        released, connections, guards = [], [], []
        async def acquire():
            connection = MigrationConnection()
            connections.append(connection)
            return connection
        async def release(connection): released.append(connection)
        factory = IsolatedConnectionFactory(acquire, release, IsolationPolicy('new',frozenset({'old'})))
        async def task():
            async with factory.lease() as guard:
                guards.append(guard)
                guard.require_verified()
                await asyncio.sleep(0)
        await asyncio.gather(task(), task())
        assert len({id(connection) for connection in connections}) == 2
        assert len(released) == 2
        for guard in guards:
            with pytest.raises(PermissionError): guard.require_verified()
    asyncio.run(run())


def test_reconnected_old_instance_is_rejected_and_released_before_work():
    async def run():
        released = []
        async def acquire(): return MigrationConnection('old')
        async def release(connection): released.append(connection)
        factory = IsolatedConnectionFactory(acquire,release,IsolationPolicy('new',frozenset({'old'})))
        with pytest.raises(PermissionError):
            async with factory.lease():
                pytest.fail('old instance admitted')
        assert len(released) == 1 and len(released[0].calls) == 1
    asyncio.run(run())
