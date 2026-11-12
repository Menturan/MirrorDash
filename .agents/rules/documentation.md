---
trigger: always_on
---

# Documentation Files to Keep Updated

| File | Update when... |
|------|---------------|
| `README.md` | Any high-level project goals, setup instructions, or repository layout changes |
| `ARCHITECTURE.md` | A new architectural decision is made or an existing one changes. Every decision starts with a `> **In short:**` line under its heading: a sentence or two a junior developer understands |
| `MEASUREMENTS.md` | A measurement or a check behind a decision is made: the record goes here (what, on which hardware and software, when), and the decision in `ARCHITECTURE.md` keeps only its conclusion and a link |
| `CONTRIBUTING.md` | How the core is run, tested or laid out changes |
| `HOW_IT_WORKS.md` | A part, a flow (start, a card to the screen, saving settings) or a file in its tables changes: a file moved, split or added |
| `DESIGN.md` | A design rule or token (colour, type size, spacing, radius) changes. Components are documented in `/design` (`static/design.html`; its settings section is drawn by the form's own code from the examples in `features/kiosk/design_forms.py`) |
| `MODULE_GUIDE.md` (in `mirrordash-sdk` repo) | The **module developer API** changes — new injected helpers, lifecycle hooks, config schema format, or storage conventions. **Not** for documenting specific modules. |
| `MODULE_AGENTS.md` (in `mirrordash-sdk` repo) | Any new module-specific coding rules, constraints, or scaffolding conventions are established |
| `README.md` of a module (its own repo, in `../mirrordash-modules/<name>/`) | A specific module's config keys, providers, API key instructions, or features change |
| `USER_GUIDE.md` | Something a mirror owner needs isn't self-explanatory in the admin page: setup before the admin page is reachable, wiring, the screen's on/off rules, recovery, Home Assistant. Don't describe fields the page already explains. It starts with a quick start and follows the owner's path (SD card → Wi-Fi → admin page → modules → the rest); troubleshooting goes in *When something is wrong*. |
| `AGENTS.md` | New coding rules, patterns, or constraints are established |
| `CHANGELOG.md` | A change a mirror owner would notice is committed — add a plain-language entry under `[Unreleased]` (`### OS image` for image-only changes). Hand-written; never generated from commits. |
| `RELEASING.md` | The release workflow, OIDC configurations, or pre-release checklists change |
| `.claude/skills/*/SKILL.md` | How a new module or a release is made changes |

**Diagrams are Mermaid, never ASCII art** (GitHub draws them, in light and dark, and they stay editable text): a `flowchart` for how parts fit, a `sequenceDiagram` for a flow across processes. Keep the colours of `HOW_IT_WORKS.md`: the core blue (`fill:#ddf4ff,stroke:#0969da`), the module host yellow (`#fff8c5`, `#9a6700`), the screen and the admin page grey (`#f6f8fa`, `#57606a`). A file tree stays text.

After changing any doc, heading or doc link (also in admin templates and `docs/`), run `uv run pytest -q tests/test_docs.py`: it checks that every `#anchor` lands on a heading, every relative link on a file, and every admin path (*Settings → …*) on a label.

The website's pictures are copies, not links: after changing `mirrordash_concept.png`, remake them with `cwebp -q 82 mirrordash_concept.png -o docs/concept.webp`, `cwebp -q 80 -resize 700 0 mirrordash_concept.png -o docs/concept-700.webp` and a 1.9:1 crop to `docs/og.jpg` (`convert … -crop`), and update the sizes in `docs/index.html`.

The module pictures on the start page (`docs/modules/<name>.webp`) are copies of each module's `screenshot.png` (`../mirrordash-modules/mirrordash-<name>/`): after one changes, `cwebp -q 82 screenshot.png -o docs/modules/<name>.webp` and update its `width`/`height` in `docs/index.html`. Two differ: Photos is made 800 px wide (`-resize 800 0`), and Home Assistant is the top 400 px of its picture (`-crop 0 0 293 400`), which ends after a whole row.

The website is built and published by `.github/workflows/site.yml`; `docs/guide.html` is a template that `scripts/build_site.py` fills from `USER_GUIDE.md`. Preview it with `uv run python scripts/build_site.py` and `python3 -m http.server -d build_workspace/site`.
