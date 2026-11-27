import asyncio

import pytest

from mirrordash_core.event_bus import EventBus


@pytest.mark.asyncio
async def test_event_bus_basic_publish_subscribe():
    bus = EventBus()
    received_data = []

    def callback(data):
        received_data.append(data)

    bus.subscribe("test:event", callback)
    bus.publish("test:event", "hello")
    
    assert received_data == ["hello"]

@pytest.mark.asyncio
async def test_event_bus_unsubscribe():
    bus = EventBus()
    received_data = []

    def callback(data):
        received_data.append(data)

    bus.subscribe("test:event", callback)
    bus.publish("test:event", "first")
    bus.unsubscribe("test:event", callback)
    bus.publish("test:event", "second")

    assert received_data == ["first"]

@pytest.mark.asyncio
async def test_event_bus_unsubscribe_nonexistent():
    bus = EventBus()
    
    # Should not raise exception
    bus.unsubscribe("nonexistent", lambda x: None)
    
    bus.subscribe("some_event", lambda x: None)
    # Should not raise exception
    bus.unsubscribe("some_event", lambda x: None)

@pytest.mark.asyncio
async def test_event_bus_no_duplicate_subscription():
    bus = EventBus()
    count = 0

    def callback(data):
        nonlocal count
        count += 1

    bus.subscribe("test:event", callback)
    bus.subscribe("test:event", callback)  # Duplicate subscribe

    bus.publish("test:event", "data")
    assert count == 1  # Should only run once

@pytest.mark.asyncio
async def test_event_bus_sync_callback_crashes():
    bus = EventBus()
    run_tracker = []

    def broken_callback(data):
        raise ValueError("broken callback")

    def normal_callback(data):
        run_tracker.append(data)

    bus.subscribe("test:event", broken_callback)
    bus.subscribe("test:event", normal_callback)

    # Even though broken_callback raises an exception, normal_callback should still execute
    bus.publish("test:event", "safe")
    assert run_tracker == ["safe"]

@pytest.mark.asyncio
async def test_event_bus_async_callback():
    bus = EventBus()
    future = asyncio.Future()

    async def async_callback(data):
        future.set_result(data)

    bus.subscribe("test:event", async_callback)
    bus.publish("test:event", "async_data")

    # Give the scheduler time to run the task
    result = await asyncio.wait_for(future, timeout=1.0)
    assert result == "async_data"

@pytest.mark.asyncio
async def test_event_bus_async_callback_crashes():
    bus = EventBus()
    future = asyncio.Future()

    async def broken_async_callback(data):
        raise ValueError("broken async callback")

    async def normal_async_callback(data):
        future.set_result(data)

    bus.subscribe("test:event", broken_async_callback)
    bus.subscribe("test:event", normal_async_callback)

    bus.publish("test:event", "safe_async")

    # Give the scheduler time to run tasks. The broken one logs an error, normal one sets future.
    result = await asyncio.wait_for(future, timeout=1.0)
    assert result == "safe_async"


def test_stopping_the_modules_clears_the_event_bus():
    import asyncio

    from mirrordash_core.event_bus import event_bus
    from mirrordash_core.features.modules.loader import ModuleLoader

    calls = []
    event_bus.subscribe("x:test", lambda data: calls.append(data))
    asyncio.run(ModuleLoader(None).stop_modules())  # what the module host does before each start
    event_bus.publish("x:test", 1)
    assert calls == []


def test_an_event_published_from_a_thread_reaches_an_async_subscriber():
    """A module that publishes from its own thread (asyncio.to_thread) still reaches async subscribers."""
    from mirrordash_core.event_bus import EventBus

    async def main():
        bus, got = EventBus(), asyncio.Event()

        async def on_event(data):
            got.set()
        bus.subscribe("x", on_event)
        await asyncio.to_thread(bus.publish, "x", 1)
        await asyncio.wait_for(got.wait(), 1)
    asyncio.run(main())


def _listener(bus, name):
    got = []
    bus.subscribe(name, got.append)
    return got


def test_wildcard_gets_the_event_from_every_package():
    bus = EventBus()
    got = _listener(bus, "*.heads_up")
    bus.publish("a.heads_up", 1)
    bus.publish("mirrordash_clock.heads_up", 2)
    assert got == [1, 2]


def test_wildcard_ignores_another_rest_and_longer_prefixes():
    bus = EventBus()
    got = _listener(bus, "*.heads_up")
    bus.publish("a.other", 1)
    bus.publish("a.b.heads_up", 2)
    bus.publish("heads_up", 3)
    bus.publish("a-b.heads_up", 4)
    assert got == []


def test_exact_and_wildcard_both_fire_once():
    bus = EventBus()
    exact, wild = _listener(bus, "a.heads_up"), _listener(bus, "*.heads_up")
    bus.publish("a.heads_up", "x")
    assert (exact, wild) == (["x"], ["x"])


def test_other_wildcard_forms_match_nothing_special():
    bus = EventBus()
    gots = [_listener(bus, name) for name in ("*", "a.*", "*.*")]
    bus.publish("a.heads_up", 1)
    assert gots == [[], [], []]


def test_wildcard_unsubscribe():
    bus = EventBus()
    got = []
    bus.subscribe("*.heads_up", got.append)
    bus.unsubscribe("*.heads_up", got.append)
    bus.publish("a.heads_up", 1)
    assert got == []
