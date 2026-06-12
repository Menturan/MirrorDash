# MirrorDash Release Process

This document outlines the professional release workflow for MirrorDash. Releases are automated via GitHub Actions, and packages are securely published to PyPI using OpenID Connect (OIDC) Trusted Publishing.

---

## One-Time PyPI & GitHub Linkage

Before the automated release workflow can run, you must link the GitHub repository to PyPI:

1. Log in to your account on [PyPI](https://pypi.org/).
2. Navigate to **Account Settings** -> **Publishing** -> **Add Publisher** (or add a "Pending Publisher" if the package has never been uploaded to PyPI before).
3. Select **GitHub** as the publisher type and fill in the following details:
   * **Owner**: `Menturan` (or your GitHub username/organization)
   * **Repository**: `MirrorDash`
   * **Workflow name**: `publish.yml`
   * **Environment**: `pypi` (This must match the environment name declared in the GitHub Actions job)
4. Click **Add / Save**.

---

## Release Guidelines

- **Only Release Stable Code**: Ensure all tests pass successfully before releasing.
- **Strict SemVer**: Follow [Semantic Versioning](https://semver.org/). Bumps are:
  - `patch` (e.g. `0.2.1` -> `0.2.2`) for backward-compatible bug fixes.
  - `minor` (e.g. `0.2.x` -> `0.3.0`) for new, backward-compatible features.
  - `major` (e.g. `0.x.x` -> `1.0.0`) for API-breaking changes.
- **Do Not Manually Tag Locally**: Let the GitHub Release interface create the git tag. This ensures that the GitHub Release, git tag, and PyPI package version are always perfectly aligned.

---

## Step-by-Step Release Flow

### 1. Pre-Release Checklist
1. Ensure you are on the `master` branch and have pulled the latest changes:
   ```bash
   git checkout master
   git pull origin master
   ```
2. Run the test suite to verify everything is passing:
   ```bash
   .venv/bin/pytest
   ```
3. Update the version number in [pyproject.toml](file:///home/menturan/repos/mymagicmirror/pyproject.toml):
   ```toml
   [project]
   version = "X.Y.Z" # Replace X.Y.Z with your new version (e.g. 0.2.1)
   ```
4. Update the [CHANGELOG.md](file:///home/menturan/repos/mymagicmirror/CHANGELOG.md):
   * Move the changes under `[Unreleased]` to a new version section matching your release version (e.g., `## [0.2.1] - 2026-06-12`). Use `YYYY-MM-DD` format for the date.
   * Update the comparison links at the bottom of the file (e.g. add `[0.2.1]` and update `[Unreleased]`).

### 2. Commit and Push
Commit the version bump and changelog update, then push to GitHub:
```bash
git add pyproject.toml CHANGELOG.md
git commit --no-gpg-sign -m "chore: bump version to X.Y.Z"
git push origin master
```

### 3. Create the GitHub Release
1. Navigate to the `MirrorDash` repository on GitHub.
2. On the right-hand sidebar under **Releases**, click **Draft a new release**.
3. Click **Choose a tag**, type the new version prefixed with a `v` (e.g., `v0.2.1`), and click **Create new tag on publish**.
4. Set the **Release title** to match the tag (e.g., `v0.2.1`).
5. Click **Generate release notes**. This automatically compiles the list of merged pull requests, commits, and contributors.
6. Click **Publish release**.

### 4. Verification
Once the release is published, the GitHub Actions runner will boot automatically:
1. Go to the **Actions** tab on your GitHub repository.
2. Locate and monitor the running **Publish to PyPI** workflow.
3. Once completed, verify that the new package version has been published successfully on [PyPI](https://pypi.org/project/mirrordash/).

---

## Architecture & Infrastructure Behind Releases

### GitHub Actions (OIDC)
Our release workflow uses the official PyPA action `pypa/gh-action-pypi-publish@release/v1` combined with GitHub's OIDC (OpenID Connect) provider. 

Inside [.github/workflows/publish.yml](file:///home/menturan/repos/mymagicmirror/.github/workflows/publish.yml), we request specific token write permissions:
```yaml
permissions:
  id-token: write
```
This is configured to match the registered **Trusted Publisher** on the PyPI dashboard under the `pypi` environment. This secures our publishing pipeline against credential leaks.

