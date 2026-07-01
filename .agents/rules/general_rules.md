# General Rules

## Table of Contents

- [Development Environment](#development-environment)
- [Design & Architecture](#design--architecture)

## Development Environment

0. **Always consider the development environment.** Local dev machines run on amd64 with a regular terminal — not on the production Pi. Code must work in both contexts: subprocess calls may prompt for `sudo` passwords (no TTY available in background processes), CDNs are blocked or cached differently offline, services like NetworkManager or systemd may not be running locally. Avoid hardcoding production-only paths or assumptions.

0a. **Be critical and proactive.** Do not blindly execute tasks as described if there is a cleaner, simpler, or more architecturally sound approach. If you think there is a better way of doing things (e.g., serving static files instead of building complex DOM-polling overlays), ALWAYS suggest it and guide the user towards the better solution.

0b. **Strict DevOps Build Philosophy.** When writing or modifying build and appliance scripts (e.g., `build_image.sh`, `setup_appliance.sh`), you MUST use stable, failsafe, declarative DevOps standards. 
- **Never** use "hobbyist" hacks: No shell polling loops (`sleep` or `while` checks), no string-scraping HTML for URLs (`grep | cut`), and no arbitrary terminal autologins or `.bash_profile` injections.
- **Always** use deterministic solutions: Native D-Bus event waiting (e.g., `nm-online`, `nmcli device wait`), native systemd services (`graphical.target`, `PAMName=login`), and canonical API flags (e.g., `curl -w '%{url_effective}'`). 
Build scripts must be deterministic, free of race-conditions, and mathematically proven to execute correctly without relying on timing or visual DOM layouts.

## Design & Architecture

1. **Remember to always follow the design rules.** Adherence to the visual design rules, system constraints, and accessibility requirements (such as high-contrast button readability) defined in `DESIGN.md`, `AGENTS.md`, and CSS files is mandatory when styling or modifying layouts/components.

2. **Document all new features immediately.** Any new features, configurations, or design system classes must be documented across all relevant documentation files (`ARCHITECTURE.md`, `DESIGN.md`, `MODULE_GUIDE.md`, `AGENTS.md`, and `CHANGELOG.md`) immediately to prevent documentation drift.

3. **Follow global configurations.** Always respect, load, and inherit settings defined under "global configuration" (such as language, timezone, time format, temperature/distance units, and coordinate location) when formatting, styling, or building logic for modules or core dashboard features.

4. **Minimize dependency inflation and prefer robust standard libraries.** Prefer utilizing well-known, popular, and robust packages (e.g. `Babel` for localization, `pytest` for tests) to avoid reinventing the wheel for complex domain tasks. However, remain conservative—do not add dependencies for simple utilities that can be written in a few lines of clean, native code. Make well-considered dependency decisions.

5. **Always use the mirrordash-cli scaffolder when creating new modules.** Never manually scaffold module directories from scratch. Always run the `mirrordash-cli create-module mirrordash-<name> --description "<desc>"` command to ensure a fully compatible packaging, template directory, and entry point layout structure is generated automatically.

6. **Professional Grade IoT & Consumer Simplicity.** The system must operate with professional-grade IoT reliability, designed to run continuously for years without manual intervention, memory leaks, or filesystem corruption. Every user-facing interface, including the captive portal network setup wizard, must be designed with ultimate simplicity in mind, ensuring that non-technical users (such as your parents) can use and configure the device safely, intuitively, and without command-line access.

7. **Disk Space Boundary Monitoring.** Active virtual environments (`venv_a`, `venv_b`) and all community modules reside on the persistent `/storage` partition. Any operations modifying packages (like installing or upgrading modules) must check free space (via the `/admin/disk-usage` endpoint, which monitors `/storage`). Always display a warning visual when remaining persistent storage space drops below 500MB to avoid module installation failures.

8. **Golden Image is a Production IoT Environment.** `GOLDEN_IMAGE.md` and all associated setup scripts must be authored with the same rigour as a professional, high-grade IoT production system. No source code on the device, no editable installs, no debug flags, no leftover dev credentials. Assume zero human intervention after deployment. Treat every instruction as executed by a non-expert.

9. **Documentation ↔ Script Synchronization.** Documentation files and their corresponding automation scripts are **two representations of the same truth**. When modifying one, you must immediately update the other. This applies universally: `DESIGN.md` ↔ `style.css`, `USER_GUIDE.md` ↔ admin UI code, `ARCHITECTURE.md` ↔ actual code patterns, module `README.md` ↔ module source.