# SPDX-License-Identifier: AGPL-3.0-only
"""What `async with self.video_window(url=...) as video:` gives a module: how its video is doing (ARCHITECTURE #34).

The core runs the player (features/video/playback.py) and sends the module its events; this is the
module's side of them, in its own process."""
import asyncio


class VideoPlayback:
    def __init__(self):
        self.playing = asyncio.Event()   # set when the first picture shows
        self.finished = asyncio.Event()  # set when the video is over, however
        self.failed = False              # it ended with an error (reason says which), not at its end or by a stop
        self.reason = ""                 # why it ended: "end of stream", "its card is hidden", the error text, ...

    def event(self, message: dict) -> None:
        """An event from the core: {"event": "playing" | "ended" | "failed", "reason": ...}."""
        if message.get("event") == "playing":
            self.playing.set()
        elif message.get("event") in ("ended", "failed"):
            self.failed = message["event"] == "failed"
            self.reason = str(message.get("reason", ""))
            self.finished.set()

    async def started(self) -> bool:
        """Wait for the first picture: True when it shows, False when the video ended before that."""
        waits = [asyncio.ensure_future(event.wait()) for event in (self.playing, self.finished)]
        try:
            await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for wait in waits:
                wait.cancel()
        return self.playing.is_set()
