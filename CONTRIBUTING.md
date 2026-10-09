# Contributing to MirrorDash

This repository is the **core**: the server, the mirror's screen, the admin page and the OS image. To make something the mirror shows, write a **module** instead. It lives in its own repository, and the [SDK](https://github.com/Menturan/mirrordash-sdk) sets one up in a minute.

## Get started

You need [uv](https://docs.astral.sh/uv/). It installs the right Python (3.14) by itself.

```bash
git clone https://github.com/Menturan/MirrorDash.git && cd MirrorDash
uv sync
MIRRORDASH_DEV=1 uv run python -m mirrordash_core.main
```

- **The mirror's screen** is at <http://localhost:8000>, and **the admin page** is at <http://localhost:8000/admin>. The first visit asks you to set an admin password.
- **Dev mode reloads** the server whenever you save a `.py`, `.html`, `.json` or `.css` file.
- **Settings are stored** in `~/.mirrordash/`.
- **What doesn't run on a computer:** anything that needs the Pi, such as the screen's power, GPIO and Wi-Fi. It fails quietly or says so.
- **To try a change on a real mirror,** publish a test version (see [RELEASING.md](RELEASING.md)) and turn on *Settings → Updates → Test versions* on the mirror.

## Where things are

| Path | What |
|---|---|
| `mirrordash_core/features/<feature>/` | One folder per feature (modules, settings, hardware, power, backup, updates, wifi, kiosk, …): its routes, logic and templates |
| `mirrordash_core/app.py` | Puts the features together |
| `mirrordash_core/admin.py`, `forms.py`, `venv.py`, `host.py`, `config.py`, … | What several features share: login and page events, forms from a schema, the A/B venvs, OS commands, the config |
| `mirrordash_core/templates/admin.html` | The admin page's shell; its CSS and JS are in `static/admin/` |
| `mirrordash_core/static/` | The mirror's screen (`index.html`, `js/kiosk/`, `style.css`), and `modules.css` with `/design`, the component library |
| `scripts/` | The OS image build and `release.py` |

## Tests

```bash
uv run pytest -q                            # everything, a few seconds
uv run playwright install chromium webkit   # once, for the browser tests
uv run pytest tests/test_visual_admin.py    # admin page in Chromium, the mirror's pages in WebKit (like the Pi's Cog)
```

Logic that isn't trivial gets one small test that fails if the logic breaks.

## How we work

- **Less is more.** Reuse what's already here, then the standard library, and only then write new code. No new dependency without a good reason. The full list is in [AGENTS.md](AGENTS.md).
- **Follow the look.** Everything in [DESIGN.md](DESIGN.md) applies to both the mirror and the admin page.
- **Know the history.** Read [ARCHITECTURE.md](ARCHITECTURE.md) before you change how something works, and add a decision there when you make one.
- **Mind the Pi.** The root is read-only on the Pi, so data goes in `~/.mirrordash`. A new `sudo` command also needs a line in `/etc/sudoers.d/mirrordash` (`scripts/setup_appliance.sh` and `GOLDEN_IMAGE.md`).
- **Commits** follow `type(scope): what changed` (`fix(admin): …`, `feat(kiosk): …`, `docs: …`). Anything a user notices also gets a line in [CHANGELOG.md](CHANGELOG.md) under *Unreleased*.

The detailed rules are in `.agents/rules/`.
