# SPDX-License-Identifier: AGPL-3.0-only
"""How the cards fade in when the screen wakes and out when it rests (ARCHITECTURE #61).

The owner's settings (system.display_control.fade) are turned into the numbers the page needs, in the
screen message (privacy.py), and into how long the output waits before it goes off. Rest is the wake
in reverse order at 60 % of the time and half the step, the look the owner approved; the factors are
here and nowhere else (the page gets the finished numbers)."""

DEFAULT_FADE = {"style": "stagger", "step_ms": 450, "duration_ms": 2500, "start_delay_ms": 0, "fade_out": True}
STYLES = ("none", "together", "stagger", "stagger_still")  # at once, all together, one by one, one by one without movement
RISE_PX = 8  # how far a card rises while it fades in (one by one)
REST_STEP, REST_TIME = 0.5, 0.6


def fade_message(display_cfg: dict) -> dict:
    """The settings as the page reads them (js/kiosk/core.js applyFade), in milliseconds. A saved value that
    isn't right (the form refuses those) is the default."""
    saved = display_cfg.get("fade") if isinstance(display_cfg.get("fade"), dict) else {}
    fade = {**DEFAULT_FADE, **saved}
    style = fade["style"] if fade["style"] in STYLES else DEFAULT_FADE["style"]
    if style == "none":
        return {"style": style}
    step, duration, delay = (fade[key] if isinstance(fade[key], int) and not isinstance(fade[key], bool) and fade[key] >= 0
                             else DEFAULT_FADE[key] for key in ("step_ms", "duration_ms", "start_delay_ms"))
    step = 0 if style == "together" else step
    out = fade["fade_out"] is not False
    return {"style": style, "rise": RISE_PX if style == "stagger" else 0, "step": step, "duration": duration,
            "delay": delay, "rest_step": round(step * REST_STEP) if out else 0,
            "rest_duration": round(duration * REST_TIME) if out else 0}


def rest_wait(fade: dict, cards: int) -> float:
    """Seconds the fade-out takes with this many cards: the last one starts after the others and then
    fades for its time. 0 when it is off. ponytail: the page doubles its times under prefers-reduced-motion
    and this doesn't know; the mirror's kiosk never sets it. Upgrade: the page says when it is done."""
    if fade["style"] == "none":
        return 0.0
    return (max(cards - 1, 0) * fade["rest_step"] + fade["rest_duration"]) / 1000
