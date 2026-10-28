---
trigger: always_on
---

# Documentation Files to Keep Updated

| File | Update when... |
|------|---------------|
| `README.md` | Any high-level project goals, setup instructions, or repository layout changes |
| `ARCHITECTURE.md` | A new architectural decision is made or an existing one changes |
| `CONTRIBUTING.md` | How the core is run, tested or laid out changes |
| `DESIGN.md` | A design rule or token (colour, type size, spacing, radius) changes. Components are documented in `/design` (`static/design.html`) |
| `MODULE_GUIDE.md` (in `mirrordash-sdk` repo) | The **module developer API** changes — new injected helpers, lifecycle hooks, config schema format, or storage conventions. **Not** for documenting specific modules. |
| `MODULE_AGENTS.md` (in `mirrordash-sdk` repo) | Any new module-specific coding rules, constraints, or scaffolding conventions are established |
| `README.md` of a module (its own repo, in `../mirrordash-modules/<name>/`) | A specific module's config keys, providers, API key instructions, or features change |
| `USER_GUIDE.md` | Something a mirror owner needs isn't self-explanatory in the admin page: setup before the admin page is reachable, wiring, the screen's on/off rules, recovery, Home Assistant. Don't describe fields the page already explains. It starts with a quick start and follows the owner's path (SD card → Wi-Fi → admin page → modules → the rest); troubleshooting goes in *When something is wrong*. |
| `AGENTS.md` | New coding rules, patterns, or constraints are established |
| `CHANGELOG.md` | A change a mirror owner would notice is committed — add a plain-language entry under `[Unreleased]` (`### OS image` for image-only changes). Hand-written; never generated from commits. |
| `RELEASING.md` | The release workflow, OIDC configurations, or pre-release checklists change |
| `.claude/skills/*/SKILL.md` | How a new module or a release is made changes |

After changing any doc, heading or doc link (also in admin templates and `docs/`), run `uv run pytest -q tests/test_docs.py`: it checks that every `#anchor` lands on a heading, every relative link on a file, and every admin path (*Settings → …*) on a label.

The website's pictures are copies, not links: after changing `mirrordash_concept.png`, remake them with `cwebp -q 82 mirrordash_concept.png -o docs/concept.webp`, `cwebp -q 80 -resize 700 0 mirrordash_concept.png -o docs/concept-700.webp` and a 1.9:1 crop to `docs/og.jpg` (`convert … -crop`), and update the sizes in `docs/index.html`.
