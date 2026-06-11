# Changelog

All notable changes to the MirrorDash project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-05-30

### Features
- Auto-inject render_template helper for custom modules
- Automate jinja2 configuration in module scaffolding
- Support filtering module logs by specific module in UI and API
- Add module loop templates logging, clock module logging, and admin API logging
- Add new Log tab displaying system, module, and host OS logs
- Add mirrordash-cli module generator to automate custom module packaging
- Automate naming normalization and support synchronous run_loops safely
- Automatically pre-create and inject writeable data_dir parameter for modules
- Support standalone JSON config schema files and migrate clock module schema
- Move module updates to separate page-level Updates tab and resolve visual editor hangs
- Initial commit with modular architecture, optimized remounts, and visual config editor

### Bug Fixes
- Resolve onload DOM element selection ordering error by placing modal above script
- Resolve JavaScript error from non-existent .title() call and add robust rendering error boundaries

### Refactoring
- Migrate clock module layout rendering to Jinja2 template

### Documentation
- Document auto-injected render_template helper and overrides
- Document mirrordash-cli automated scaffolding in module guide
- Relocate developer gotchas into their respective chapters and remove section 7
- Add section on common gotchas and best practices for module developers

### Chores
- Add jinja2 to dependencies in pyproject.toml

[Unreleased]: https://github.com/Menturan/MirrorDash/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Menturan/MirrorDash/releases/tag/v0.1.0
