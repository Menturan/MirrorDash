# AGENTS.md — MirrorDash AI Agent Guide

This file provides AI coding agents with everything they need to work effectively on the MirrorDash codebase. See `.agents/rules/` for detailed rule files.

> [!IMPORTANT]
> **Remember to always follow the design rules.** Adherence to the visual design rules, system constraints, and aesthetic/contrast guidelines is critical. See `DESIGN.md` and `mirrordash_core/static/style.css` for details.

## Overview

MirrorDash is a modular, ambient information display system designed for Raspberry Pi running in Kiosk mode. The backend is a FastAPI/Python server. The frontend is pure HTML, CSS, and JavaScript served to Cog (WebKit) in full-screen kiosk mode.

**Key principle:** The mirror is a passive, glanceable display — not an interactive application.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.14, FastAPI, Uvicorn |
| Package Management | `uv` (not pip directly) |
| Frontend | Vanilla HTML5, CSS Grid, Vanilla JS (frameworks require justification) |
| Templating | Jinja2 (server-side, rendered per module) |
| Real-time | WebSockets (one persistent connection per browser client) |
| Module System | Python `importlib.metadata` entry points (`mirrordash.modules` group) |
| Deployment | Raspberry Pi OS Trixie (Debian 13), OverlayFS read-only rootfs, Wayland (labwc), Cog kiosk |

## Rules Directory

| File | Contents |
|------|----------|
| `.agents/rules/coding_rules.md` | Python patterns, frontend constraints, git conventions |
| `.agents/rules/general_rules.md` | Development environment, IoT simplicity, design standards |
| `.agents/rules/architecture.md` | Core modification patterns, design system |
| `.agents/rules/documentation.md` | Documentation file update reference table |