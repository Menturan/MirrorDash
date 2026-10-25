---
name: new-module
description: Create a new MirrorDash module (a panel on the mirror) with the local SDK in ../mirrordash-sdk, build and test it, try it on a local mirror, and publish it as its own GitHub repo. Use when asked to make, scaffold or add a new module or widget.
---

# Create a new MirrorDash module

A module is its own Python package and its own GitHub repo (`Menturan/mirrordash-<name>`), not part of this
repo. The SDK and the modules are sibling folders of this repo's main checkout:

```bash
REPOS=$(dirname "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")")  # works from a worktree too
SDK=$REPOS/mirrordash-sdk          # use this SDK, not `uvx mirrordash-sdk` (PyPI can lag behind it)
MODULES=$REPOS/mirrordash-modules  # one folder (and git repo) per module
```

## 1. Understand it first

- Read `$SDK/MODULE_AGENTS.md` (the rules) before writing anything; look up details in `$SDK/MODULE_GUIDE.md`
  (fetching, settings, translations, testing, sharing).
- Pin down with the user, if the request doesn't say: what it shows, where the data comes from, whether it
  needs an API key, what the user can set. Name: `mirrordash-<name>`, lowercase with hyphens.
- The scaffold has every part a module can have, marked `── <part> ──`; plan which parts the module keeps.
- Look at the closest existing module in `$MODULES` and copy its patterns (`mirrordash-namnsdag`: small,
  no key; `mirrordash-weather`: API with a key; `mirrordash-news`: RSS via `self.fetch`).

## 2. Scaffold

```bash
cd "$MODULES" && uv run --project "$SDK" mirrordash-sdk create-module mirrordash-<name> -d "<one-line description>" -a Menturan
```

This makes the package, entry point, `config_schema.json`, `translations/`, tests and `git init`. Never build
the layout by hand.

## 3. Build it

First delete the parts the module doesn't need, each with its settings, texts and tests (the table in the
module's README), and the README sections for them. Then follow `MODULE_AGENTS.md`; the usual misses:

- Fetch only with `self.fetch_json` / `self.fetch`, with `max_age=self.interval`; show `data` when there is any
  and a `.module-message` when `error` is set. No render time in the HTML.
- Markup from the component library (`http://localhost:8000/design`); no own background, no fixed widths,
  Lucide icons, scripts query from `root`.
- Settings in `config_schema.json`; keys get `"format": "password"`. Don't declare the core's own keys
  (`enabled`, `position`, …).
- `translations/en.json` and `sv.json`; nothing hardcoded that `globals` gives (language, timezone, units).
- Dependencies in `pyproject.toml` only for what `plugin.py` imports that the mirror doesn't provide.

## 4. Check it

```bash
cd "$MODULES/mirrordash-<name>" && uv run pytest -q && uv run --project "$SDK" mirrordash-sdk validate .
```

Extend the generated tests to cover the module's own logic (mock fetches with
`module.fetch_json = AsyncMock(return_value=(data, None))`), including the error and empty cases.

## 5. Try it on the mirror

From this repo (the core), against the current core code:

```bash
uv sync --extra dev --inexact && uv pip install -e "$MODULES/mirrordash-<name>"
MIRRORDASH_DEV=1 uv run python -m mirrordash_core.main   # admin: http://localhost:8000/admin → Modules → enable it
```

Check it at `http://localhost:8000/` in a narrow and a wide position, in Swedish and English, and with the
network off (the error state). Save a `screenshot.png` of the module at the module's root (Playwright is in
the dev dependencies). Stop the server afterwards.

## 6. README

`README.md` at the module root: what it shows, step by step how to get any API key and where to enter it,
the settings, and the screenshot (`![Screenshot](screenshot.png)`).

## 7. Publish

Ask the user before this step: it creates a public repo.

```bash
cd "$MODULES/mirrordash-<name>"
git add . && git commit -m "feat: first version of the <name> module"
gh repo create Menturan/mirrordash-<name> --public --source . --push --description "<About text: what it shows>"
```

The description is the About text mirrors show in their module list. Then make the first release with the
`release` skill (module, version `1.0.0`; mirrors only see modules that have a GitHub Release).

Last, in this repo: add a row to the **Modules** table in `README.md` (link + what it shows), commit and push.
