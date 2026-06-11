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
