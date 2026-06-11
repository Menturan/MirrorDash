import pytest
import asyncio
from mirrordash_core.ws_manager import ConnectionManager

class MockWebSocket:
    def __init__(self, should_fail=False):
        self.accepted = False
        self.sent_messages = []
        self.should_fail = should_fail

    async def accept(self):
        self.accepted = True

    async def send_json(self, message):
        if self.should_fail:
            raise Exception("connection lost")
        self.sent_messages.append(message)

@pytest.mark.asyncio
async def test_ws_manager_connect():
    manager = ConnectionManager()
    ws = MockWebSocket()
    
    await manager.connect(ws)
    
    assert ws.accepted is True
    assert ws in manager.active_connections
    assert len(manager.active_connections) == 1

@pytest.mark.asyncio
async def test_ws_manager_disconnect():
    manager = ConnectionManager()
    ws = MockWebSocket()
    
    await manager.connect(ws)
    assert ws in manager.active_connections
    
    manager.disconnect(ws)
    assert ws not in manager.active_connections
    assert len(manager.active_connections) == 0

@pytest.mark.asyncio
async def test_ws_manager_disconnect_nonexistent():
    manager = ConnectionManager()
    ws = MockWebSocket()
    
    # Should not raise exception
    manager.disconnect(ws)

@pytest.mark.asyncio
async def test_ws_manager_broadcast_caching():
    manager = ConnectionManager()
    
    # Message with module & html should be cached
    msg_cacheable = {"module": "mirrordash-clock", "html": "<div>12:00</div>", "position": "top_left"}
    await manager.broadcast(msg_cacheable)
    assert manager.latest_messages["mirrordash-clock"] == msg_cacheable
    
    # Message without module should not be cached in latest_messages
    msg_non_cacheable = {"action": "reload"}
    await manager.broadcast(msg_non_cacheable)
    assert "reload" not in manager.latest_messages
    assert len(manager.latest_messages) == 1

@pytest.mark.asyncio
async def test_ws_manager_replay_cache_on_connect():
    manager = ConnectionManager()
    
    msg1 = {"module": "mirrordash-clock", "html": "<div>12:00</div>", "position": "top_left"}
    msg2 = {"module": "mirrordash-weather", "html": "<div>Sunny</div>", "position": "top_right"}
    
    await manager.broadcast(msg1)
    await manager.broadcast(msg2)
    
    # Connect new client
    ws = MockWebSocket()
    await manager.connect(ws)
    
    # Verify that the new client immediately received both cached messages
    assert len(ws.sent_messages) == 2
    assert msg1 in ws.sent_messages
    assert msg2 in ws.sent_messages

@pytest.mark.asyncio
async def test_ws_manager_broadcast_dead_connection_cleanup():
    manager = ConnectionManager()
    
    ws_good = MockWebSocket()
    ws_bad = MockWebSocket(should_fail=True)
    
    await manager.connect(ws_good)
    await manager.connect(ws_bad)
    
    assert len(manager.active_connections) == 2
    
    # Broadcast a message
    msg = {"module": "test", "html": "<div>test</div>"}
    await manager.broadcast(msg)
    
    # Verify good socket received the message
    assert len(ws_good.sent_messages) == 1
    assert ws_good.sent_messages[0] == msg
    
    # Verify bad socket was automatically cleaned up and disconnected
    assert ws_good in manager.active_connections
    assert ws_bad not in manager.active_connections
    assert len(manager.active_connections) == 1

@pytest.mark.asyncio
async def test_ws_manager_clear_cache():
    manager = ConnectionManager()
    msg = {"module": "mirrordash-clock", "html": "<div>12:00</div>"}
    
    await manager.broadcast(msg)
    assert "mirrordash-clock" in manager.latest_messages
    
    manager.clear_cache()
    assert len(manager.latest_messages) == 0




