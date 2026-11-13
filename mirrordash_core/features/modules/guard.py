# SPDX-License-Identifier: AGPL-3.0-only
"""The screen's guard against a crash loop (ARCHITECTURE #47). A setting that makes WebKit's renderer crash
(out of GPU memory, 2026-11-04) would crash it again after every restart of the screen, so the page tells the
core when it's a fresh load (its page id), and a fresh load the core didn't ask for is a crash. Two crashes
within WINDOW, and the guard pauses modules in steps, one step for each crash within WINDOW of the last step:

1. the module instance installed, updated or saved last;
2. also every instance in Background or Foreground, and those that played a video since the core started;
3. everything but the clock, and the screen says so (the message at the bottom edge, in its language).

The pause is saved (a restart would only start the loop again) until the owner presses Show Again on the
Dashboard or saves a paused module's settings, which lets that one go."""

import logging
import time

from mirrordash_core.config import load_config, save_config

logger = logging.getLogger("mirrordash.core.guard")

KEY = "screen_guard"
WINDOW = 10 * 60  # seconds: two crashes in it start the guard, and one in it after a step takes the next
LAYERS = ("background", "foreground")
CLOCK_MODULES = ("mirrordash-clock", "mirrordash_clock")
TOP = 3
# The screen's message at step 3, in the mirror's language (the core has no translations of its own)
MESSAGE = {
    "en": ("The screen crashed several times",
           "The modules are paused so the mirror keeps working. Open the admin page to see what happened."),
    "sv": ("Skärmen kraschade flera gånger",
           "Modulerna är pausade så att spegeln fortsätter att fungera. Öppna admin-sidan för att se vad som hände."),
}


def state(config: dict) -> dict:
    guard = config.get(KEY)
    return guard if isinstance(guard, dict) else {}


def level(config: dict) -> int:
    value = state(config).get("level")
    return value if isinstance(value, int) and 0 <= value <= TOP else 0


def paused(config: dict) -> set[str]:
    return {name for name in state(config).get("paused") or [] if isinstance(name, str)}


def _times(values) -> dict[str, float]:
    return {k: v for k, v in (values or {}).items() if isinstance(k, str) and isinstance(v, int | float)}


def last_changed(config: dict) -> str | None:
    """The instance installed, updated or saved last: its own saves, and its package's installs (matched by the
    package's name in what was installed, a PyPI name or a GitHub URL). None when it can't be told: nothing
    recorded, or a tie (everything updated at once)."""
    from mirrordash_core.features.modules.loader import find_entry_point
    guard, modules = state(config), config.get("modules") or {}
    saved, installed = _times(guard.get("changed")), _times(guard.get("installed"))
    when = {}
    for name, cfg in modules.items():
        if not isinstance(cfg, dict) or not cfg.get("enabled", True) or name in paused(config):
            continue
        ep = find_entry_point(cfg.get("module", name))
        dist = (ep.dist.name if ep and ep.dist else "").lower().replace("_", "-")
        # ponytail: a name match; a package installed under another name than its repo's isn't found
        times = [saved.get(name, 0)] + [t for package, t in installed.items()
                                        if package == "*" or (dist and dist in package.lower().replace("_", "-"))]
        when[name] = max(times)
    if not when or max(when.values()) == 0:
        return None
    best = max(when.values())
    latest = [name for name, t in when.items() if t == best]
    return latest[0] if len(latest) == 1 else None


def to_pause(config: dict, step: int, played_video: set[str]) -> set[str]:
    """What a step pauses (with the steps before it)."""
    modules = {name: cfg for name, cfg in (config.get("modules") or {}).items() if isinstance(cfg, dict)}
    if step >= TOP:
        return {name for name, cfg in modules.items() if cfg.get("module", name) not in CLOCK_MODULES}
    names = set()
    if step >= 1 and (last := last_changed(config)):
        names.add(last)
    if step >= 2:
        names |= {name for name, cfg in modules.items() if cfg.get("position") in LAYERS} | (played_video & set(modules))
    return names


def after_crash(config: dict, now: float, played_video: set[str]) -> dict:
    """The guard's state after a crash at `now`: the crash noted, and the next step if it's due. A step that
    would pause nothing new (step 1 with no module changed last) goes on to the next."""
    guard = dict(state(config))
    crashes = [t for t in guard.get("crashes") or [] if isinstance(t, int | float) and now - t <= WINDOW] + [now]
    step, stepped_at = level(config), guard.get("stepped_at") or 0
    due = len(crashes) >= 2 if step == 0 else now - stepped_at <= WINDOW
    names = paused(config)
    if due and step < TOP:
        step += 1
        while step < TOP and not to_pause(config, step, played_video) - names:
            step += 1
        new = to_pause(config, step, played_video) - names
        names |= new
        # what each step paused, for the Dashboard's banner
        by_step = {**(guard.get("by_step") if isinstance(guard.get("by_step"), dict) else {}), str(step): sorted(new)}
        guard.update(level=step, stepped_at=now, since=guard.get("since") or crashes[0], by_step=by_step)
        logger.warning(f"The screen crashed {len(crashes)} times in {WINDOW // 60} minutes: step {step}, paused {sorted(names)}")
    # the crashes since the guard began this time: those that started it, then one more each
    count = 0 if not step else len(crashes) if not level(config) else (guard.get("count") or 0) + 1
    guard.update(crashes=crashes, paused=sorted(names), count=count)
    return guard


def note(kind: str, name: str, now: float | None = None) -> None:
    """A save ("changed", an instance) or an install or update ("installed", a package, "*" for all)."""
    config = load_config()
    guard = dict(state(config))
    guard[kind] = {**_times(guard.get(kind)), name: now or time.time()}
    config[KEY] = guard
    save_config(config)


def release(instance_id: str) -> bool:
    """A paused module's settings were saved: it comes back. With nothing left paused the guard starts over."""
    config = load_config()
    names = paused(config)
    if instance_id not in names:
        return False
    names.discard(instance_id)
    guard = dict(state(config))
    guard["paused"] = sorted(names)
    if not names:
        guard.update(level=0, crashes=[], stepped_at=0, since=0, count=0, by_step={})
    config[KEY] = guard
    save_config(config)
    return True


def screen_part(config: dict) -> dict:
    """What the screen message carries of it: the step, and at the top step the message in the mirror's language."""
    step = level(config)
    language = (config.get("globals") or {}).get("language", "en")
    title, body = MESSAGE.get(language, MESSAGE["en"])
    return {"guard": step, "guard_message": {"title": title, "body": body} if step >= TOP else None}


def banner(config: dict) -> dict | None:
    """What the Dashboard's banner says (admin_dashboard.html): the step, the names each step paused, and when
    the screen crashed, on the owner's clock. None while the guard rests."""
    from datetime import datetime

    from mirrordash_core.features.modules.service import instance_title
    from mirrordash_core.features.power.display_power import local_now
    step = level(config)
    if not step:
        return None
    guard, modules, zone = state(config), config.get("modules") or {}, local_now(config).tzinfo
    by_step = guard.get("by_step") if isinstance(guard.get("by_step"), dict) else {}

    def titles(names) -> list[str]:
        return [instance_title(name, modules.get(name) if isinstance(modules.get(name), dict) else {}) for name in names or []]

    def clock(t) -> str:
        return datetime.fromtimestamp(t, zone).strftime("%Y-%m-%d %H:%M") if isinstance(t, int | float) and t else ""

    return {"level": step, "first": titles(by_step.get("1")), "more": titles(by_step.get("2")),
            "crashes": [clock(t) for t in guard.get("crashes") or []], "count": guard.get("count") or 0,
            "since": clock(guard.get("since"))}


async def page_crashed(played_video: set[str]) -> None:
    """The screen's page came back without being asked to: noted, and the modules paused if a step is due."""
    from mirrordash_core.features.modules.client import module_host
    from mirrordash_core.features.power.privacy import broadcast_screen
    config = load_config()
    before = (level(config), paused(config))
    after = after_crash(config, time.time(), played_video)
    if not before[0] and after.get("level"):
        # The guard's first step is due, and modules were updated minutes ago: their versions before go back
        # instead of anything being paused (modules/trial.py), and the guard starts over with them
        from mirrordash_core.features.modules import trial
        from mirrordash_core.host import run_in_background, run_restart
        if await trial.put_back():
            config = load_config()  # put_back saved its own part
            config[KEY] = {**state(config), "crashes": []}
            save_config(config)
            run_in_background(run_restart())
            return
    config[KEY] = after
    save_config(config)
    if (level(config), paused(config)) != before:
        await broadcast_screen(config)
        await module_host.reload_modules()


async def show_again() -> None:
    """The Dashboard's Show Again: every paused module back, the count from zero (the save times stay)."""
    from mirrordash_core.features.modules.client import module_host
    from mirrordash_core.features.power.privacy import broadcast_screen
    config = load_config()
    guard = dict(state(config))
    guard.update(level=0, paused=[], crashes=[], stepped_at=0, since=0, count=0, by_step={})
    config[KEY] = guard
    save_config(config)
    await broadcast_screen(config)
    await module_host.reload_modules()
