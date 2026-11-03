# SPDX-License-Identifier: AGPL-3.0-only
"""Privacy mode (ARCHITECTURE #45): only the modules the owner marked "Shown in Privacy Mode" stay on the
screen (the clock, the weather, a guest album); with none marked, the MirrorDash monogram or nothing.
Turned on and off from the Power tab, the mirror's button and Home Assistant (MQTT). Turning the screen off
was already there and is the most neutral a mirror can be; this keeps what's harmless.

What the screen shows of it comes in one message with the other screen-wide settings, {"type": "screen"}:
when the page connects (kiosk/ws.py), when the settings are saved and when the mode changes."""

from mirrordash_core.config import load_config, save_config
from mirrordash_core.features.settings.schema import CARDS_OVER_PHOTO

PRIVACY_SHOWS = ("monogram", "nothing")  # with no module marked; the first is the default


def privacy_on(config: dict | None = None) -> bool:
    """Whether it's on. Kept apart from "globals", which the Settings tab saves whole from its form."""
    privacy = (config if config is not None else load_config()).get("privacy")
    return isinstance(privacy, dict) and privacy.get("on") is True


def screen_message(config: dict | None = None) -> dict:
    """The screen-wide settings js/kiosk/core.js puts on <html>: privacy mode and what it shows, and how
    the modules look over a background photo (an unknown value: the default)."""
    config = config if config is not None else load_config()
    globals_config = config.get("globals") or {}
    shows, cards = globals_config.get("privacy_shows"), globals_config.get("cards_over_photo")
    return {"type": "screen", "privacy": privacy_on(config),
            "privacy_shows": shows if shows in PRIVACY_SHOWS else PRIVACY_SHOWS[0],
            "cards_over_photo": cards if cards in CARDS_OVER_PHOTO else CARDS_OVER_PHOTO[0]}


async def set_privacy(on: bool) -> None:
    """Privacy mode on or off: saved (it outlasts a restart while the guests are still there), and the
    screen told at once. Home Assistant's switch reads it at the bridge's next pass."""
    from mirrordash_core.features.kiosk.ws import manager
    config = load_config()
    if privacy_on(config) != on:
        config["privacy"] = {"on": on}
        save_config(config)
    await manager.broadcast(screen_message(config))


async def toggle_privacy() -> None:
    await set_privacy(not privacy_on())
