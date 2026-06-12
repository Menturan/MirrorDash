# 🪞 MirrorDash

[![Python Version](https://img.shields.io/badge/python-3.14%2B-blue.svg)](#)
[![License: PolyForm_NC_1.0.0](https://img.shields.io/badge/license-PolyForm_NC_1.0.0-525252)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi%20%2F%20Linux-orange.svg)](#)
[![PyPI version](https://img.shields.io/pypi/v/mirrordash.svg)](https://pypi.org/project/mirrordash/)
[![Downloads](https://pepy.tech/badge/mirrordash)](https://pepy.tech/project/mirrordash)

**The ambient, zero-friction smart display designed to bring your home to life—silently, elegantly, and beautifully.**

Imagine a morning routine without screens, clutter, or notification fatigue. You walk into your bathroom or hallway, look in the mirror, and see the time, today's weather forecast, and your upcoming calendar appointments floating gently on the glass. No buttons to press, no voice assistants to argue with, and no touchscreen smudges. 

That is **MirrorDash**—a professional-grade, ambient smart display that turns any two-way mirror into a quiet, glanceable dashboard.

---

![MirrorDash Ambient Smart Display Preview](https://raw.githubusercontent.com/Menturan/MirrorDash/master/mirrordash_concept.png)

---

## ✨ Why MirrorDash is Different

While smart mirrors and screens have existed for years, they are notoriously difficult to set up, maintain, and develop for. MirrorDash is built from the ground up to solve these pain points, offering a premium experience that sets it apart from traditional platforms:

* **🛡️ True "Set and Forget" Reliability**: Standard DIY smart displays frequently corrupt their SD cards due to constant writing of logs and cache files. MirrorDash is built to run on write-restricted, read-only operating systems (OverlayFS). Dynamic configurations are isolated to a protected, persistent partition, ensuring your display runs continuously for years without filesystem degradation or SD card wear.
* **📱 No-Code Phone Configuration**: Most open-source smart mirror platforms require you to edit complicated configuration files using terminal commands or SSH. MirrorDash provides an elegant, local Admin Dashboard that lets you change settings, toggle widgets, enter API keys, and manage backups directly from your phone.
* **⚡ Ultra-Lightweight, Zero-Build Frontend**: Unlike modern web setups that rely on heavy frameworks (like React or Vue) and bloat the CPU, MirrorDash uses pure, native HTML, CSS, and JavaScript connected via direct WebSockets. By eliminating heavy build steps and runtimes, the interface updates instantly with near-zero CPU and memory overhead on the Raspberry Pi.
* **🧩 Decoupled Widget Ecosystem**: Instead of a massive, messy codebase where one broken module crashes the entire system, MirrorDash treats every widget as a fully isolated, standard Python package. Developers can build, test, and distribute widgets independently using the MirrorDash SDK without touching the core system.

---

![MirrorDash Ambient Smart Display Preview](https://raw.githubusercontent.com/Menturan/MirrorDash/master/screenshot.png)

---

## ⚙️ Technical Deep-Dive (For Developers)

Under the hood, MirrorDash is a production-grade IoT platform optimized for the Raspberry Pi running in full-screen kiosk mode.

* **🐍 Backend**: FastAPI (Python 3.14) driving a high-performance, asynchronous event loop. Communication with the frontend is handled via real-time WebSockets with local frame caching for instant layout rendering on client connect.
* **🎨 Frontend**: Zero-framework vanilla HTML5, CSS Grid/Flexbox, and native JavaScript. No React, Vue, or heavy build steps—meaning near-zero CPU and memory footprint on the Pi.
* **🔒 OS Hardening**: Configured to run on a read-only OverlayFS root filesystem (Debian 13 Trixie). Dynamic configs and data are written exclusively to a persistent user partition, preventing SD card corruption from frequent write cycles.
* **🏗️ Decoupled Architecture**: Widgets are fully isolated, pip-installable Python packages discovered at runtime via metadata entry points.

---

## 🚀 Build for MirrorDash (Pushing Community Modules)

Do you have a smart device, a custom API, or a unique idea you want to show on your mirror? MirrorDash is built for developers. We invite you to build and share custom widgets with the community!

Our standalone **MirrorDash SDK** makes bootstrapping a new widget incredibly easy:

1. **🛠️ Scaffold a new module in seconds**:
   ```bash
   uvx mirrordash-cli create-module mirrordash-my-widget --description "My custom display widget"
   ```
2. **💻 Develop with standard tools**: Write simple Python logic to fetch data, and structure your layout with a clean Jinja2 HTML template and vanilla CSS.
3. **📦 Share it**: Packages are standard Python distributions. You can publish your module to PyPI, host it on GitHub, or install it locally on your mirror.

Whether you want to show transit schedules, air quality indexes, plant soil moisture, or custom stock charts, the community is always looking for new modules. Check out [mirrordash-sdk](file:///home/menturan/repos/mirrordash-sdk) to get started!
