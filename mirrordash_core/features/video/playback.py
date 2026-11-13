# SPDX-License-Identifier: AGPL-3.0-only
"""A module's `async with self.video_window(url=...)` block as the core runs it (ARCHITECTURE #34).

hold() checks what the module asked for, puts up the window (window.py), runs the player in it (player.py)
and tells the module when the picture shows and how it ended. The block lasts as long as the module stays
in it; a player that ends first only tells the module, which decides what comes next. The core stops the
player on its own when the card is hidden (schedule, privacy mode: stop) and when the screen starts to
fade out (stop_all). The player also dies with the core (command.py, setpriv) and with a hung stream
(player.py), and the module's process dying ends the block (modules/client.py)."""
import asyncio
import logging

from mirrordash_core.features.video import command
from mirrordash_core.features.video.player import Player
from mirrordash_core.features.video.window import video_window

logger = logging.getLogger("mirrordash.core.video")

_players: dict[str, Player] = {}  # instance id -> its player while the block runs (one window at a time)


async def checked_command(options: dict) -> list[str]:
    """The player's command for what the module asked, or a ValueError that says what is wrong."""
    url = command.check_url(options.get("url"))
    output = command.check_output(options.get("output", "jack"))
    volume = command.check_volume(options.get("volume", 1.0))
    await command.check_host_is_public(url)
    return command.build_command(url, output, volume, bool(options.get("fullscreen")))


async def hold(instance_id: str, options: dict, send, ended: asyncio.Event) -> None:
    """Run one block. send(message) tells the module: {"error": ...} or {} once the window is up (the reply
    to its call), then {"event": "playing"} and at the end {"event": "ended" | "failed", "reason": ...}.
    Returns when `ended` is set, which the module does by leaving its block."""
    try:
        player_command = await checked_command(options)
    except ValueError as e:
        await send({"error": str(e)})
        return
    answered = False
    try:
        async with video_window(instance_id, fullscreen=bool(options.get("fullscreen")), hz=options.get("hz")):
            await send({})
            answered = True
            player = _players[instance_id] = Player(player_command)
            playing = asyncio.create_task(_play(player, send))
            try:
                await ended.wait()
            finally:
                player.stop("the video window was closed")
                await playing
                _players.pop(instance_id, None)
    except Exception as e:
        logger.warning(f"{instance_id}: video window failed: {e!r}")
        if not answered:
            await send({"error": "the mirror couldn't place the video window (see its log)"})


async def _play(player: Player, send) -> None:
    async def picture() -> None:
        await send({"event": "playing"})

    failed, reason = await player.run(picture)
    if failed:
        logger.warning(f"the video player failed: {reason}")
    await send({"event": "failed" if failed else "ended", "reason": reason})


def stop(instance_id: str, reason: str) -> None:
    """End an instance's video: its card is hidden."""
    if player := _players.get(instance_id):
        player.stop(reason)


def stop_all(reason: str) -> None:
    """End every video: the screen starts to fade out."""
    for player in _players.values():
        player.stop(reason)
