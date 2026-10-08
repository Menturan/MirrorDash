---
trigger: always_on
---

# Documentation Files to Keep Updated

| File | Update when... |
|------|---------------|
| `README.md` | Any high-level project goals, setup instructions, or repository layout changes |
| `ARCHITECTURE.md` | A new architectural decision is made or an existing one changes |
| `DESIGN.md` | Any CSS component is added, removed, or renamed in `style.css` |
| `MODULE_GUIDE.md` (in `mirrordash-sdk` repo) | The **module developer API** changes — new injected helpers, lifecycle hooks, config schema format, or storage conventions. **Not** for documenting specific modules. |
| `MODULE_AGENTS.md` (in `mirrordash-sdk` repo) | Any new module-specific coding rules, constraints, or scaffolding conventions are established |
| `modules/<name>/README.md` | A specific module's config keys, providers, API key instructions, or features change |
| `USER_GUIDE.md` | Something a mirror owner needs isn't self-explanatory in the admin page: setup before the admin page is reachable, wiring, the screen's on/off rules, recovery, Home Assistant. Don't describe fields the page already explains. |
| `AGENTS.md` | New coding rules, patterns, or constraints are established |
| `CHANGELOG.md` | A change a mirror owner would notice is committed — add a plain-language entry under `[Unreleased]` (`### OS image` for image-only changes). Hand-written; never generated from commits. |
| `RELEASING.md` | The release workflow, OIDC configurations, or pre-release checklists change |