# MirrorDash — Issue Tracker

This document tracks identified bugs, security vulnerabilities, and architectural improvements.

---

## Active Feature Goals & Roadmap

### 1. Professional OS Image Distribution Pipeline
* **Goal**: Provide a professional, automated, and user-friendly distribution mechanism for the compressed production MirrorDash OS image (`mirrordash-final.img.gz`).
* **Proposed Strategy**:
  1. **GitHub Releases Hosting**: Store the compressed image (`.img.gz`) and its SHA256 checksum (`.sha256`) as release assets on GitHub under tagged release versions (aligned with PyPI package versions).
  2. **Raspberry Pi Imager Integration**: Publish and host a custom OS repository JSON manifest (`mirrordash-os-list.json`) via GitHub Pages. This allows users to input the URL in Raspberry Pi Imager (`rpi-imager --repo <url>`) and flash MirrorDash directly from the official GUI.
  3. **CI/CD Build Automation**: Configure a GitHub Actions workflow using self-hosted builders or VM-based QEMU emulation to automate the build, shrink, compression, and signing of the image upon tagging a release.
  4. **Update Checks**: Implement a checks manager inside the MirrorDash admin dashboard that compares the running version against the latest GitHub release and prompts the user to trigger virtual environment updates over-the-air (OTA).

### 2. A/B Partition Over-The-Air (OTA) System Updates
* **Goal**: Enable failsafe, atomic operating system upgrades over-the-air without requiring the user to physically re-flash the SD card, while preserving all user configurations and installed modules.
* **Proposed Strategy**:
  1. **Dual System Slot Disk Layout**: Redesign the target SD card/disk partition layout to feature dual redundant system slots (Slot A: Boot A/Root A, Slot B: Boot B/Root B) alongside the shared, independent `/storage` partition.
  2. **Streaming Partition Writer**: Implement a secure background service that downloads the compressed partition image and streams it directly to the inactive slot block-by-block.
  3. **Bootloader Slot-Switching**: Configure the bootloader (e.g., Raspberry Pi's native `tryboot` mechanism or U-Boot) to manage active boot flags and coordinate automatic watchdog rollbacks to the active slot if the upgraded slot fails to boot.
  4. **Data Isolation Maintenance**: Ensure the shared `/storage` partition is mounted by both slot environments on boot, preserving all database configurations, logs, and python virtual environments across OS upgrades.


