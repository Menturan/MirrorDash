# MirrorDash Release Process

This document outlines the professional release workflow for MirrorDash. Releases are automated via GitHub Actions, and packages are securely published to PyPI using OpenID Connect (OIDC) Trusted Publishing.

---

## Release Guidelines

- **Only Release Stable Code**: Ensure all tests pass successfully before releasing.
- **Strict SemVer**: Follow [Semantic Versioning](https://semver.org/). Bumps are:
  - `patch` (0.2.x) for bug fixes.
  - `minor` (0.x.0) for new backwards-compatible features.
  - `major` (x.0.0) for breaking changes.
- **Do Not Manually Tag Locally**: Let the GitHub Release process create the git tag automatically. This keeps the GitHub Release, git tag, and PyPI version perfectly aligned.

---

## Step-by-Step Release Flow

### 1. Pre-Release Checklist
1. Ensure you are on the primary branch (e.g., `master` or `main`) and have pulled the latest changes.
2. Run the test suite to verify everything is passing:
   ```bash
   .venv/bin/pytest
   ```
3. Update the version number in [pyproject.toml](file:///home/menturan/repos/mymagicmirror/pyproject.toml):
   ```toml
   [project]
   version = "0.2.1"
   ```
4. Update the [CHANGELOG.md](file:///home/menturan/repos/mymagicmirror/CHANGELOG.md):
   * Move the changes under `[Unreleased]` to a new version section (e.g., `## [0.2.1] - YYYY-MM-DD`).
   * Update the comparison links at the bottom of the file.

### 2. Commit and Push
Commit the version bump and changelog update, then push to GitHub:
```bash
git add pyproject.toml CHANGELOG.md
git commit --no-gpg-sign -m "chore: bump version to 0.2.1"
git push origin master
```

### 3. Create the GitHub Release
1. Navigate to your repository on GitHub.
2. Under **Releases** on the right-hand sidebar, click **Draft a new release** (or **Create a new release**).
3. Click **Choose a tag** and type the version prefixed with a `v` (e.g., `v0.2.1`). Click **Create new tag on publish**.
4. Set the **Release title** (e.g., `v0.2.1`).
5. Click **Generate release notes**. This automatically compiles the list of merged pull requests, commits, and contributors.
6. Click **Publish release**.

### 4. Verification
Once the release is published, the GitHub Actions runner will boot automatically:
1. Go to the **Actions** tab on your GitHub repository.
2. Locate the running **Publish to PyPI** workflow.
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
