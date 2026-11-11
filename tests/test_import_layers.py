"""Which part of the core may import which (.agents/rules/architecture.md, "Layers").

Two things are checked, both over every import in the package, also the ones inside a function:
no two features import each other, and the shared code at the package root imports no feature.
The exceptions below are the ones left to take away: a new one fails the test, and so does one that is
gone but still listed, so the lists only shrink."""
import ast
from pathlib import Path

CORE = Path(__file__).parent.parent / "mirrordash_core"

# Features that still import each other (alphabetical in each pair)
MUTUAL = {
    ("backup", "modules"), ("backup", "wifi"), ("dashboard", "wifi"), ("hardware", "power"),
    ("logs", "updates"),
    ("modules", "mqtt"), ("modules", "power"), ("modules", "updates"),
}
# Shared code that still imports a feature. app.py puts the app together and main.py starts it: they may.
ROOT_TO_FEATURE: set[tuple[str, str]] = set()
PUTS_THE_APP_TOGETHER = {"app", "main"}


def part(module: str) -> str | None:
    """"mirrordash_core.features.power.privacy" is the feature "power"; "mirrordash_core.config" is "/config"."""
    names = module.split(".")
    if names[0] != "mirrordash_core" or len(names) < 2:
        return None
    return names[2] if names[1] == "features" and len(names) > 2 else "/" + names[1]


def imports() -> set[tuple[str, str]]:
    """Every (importing part, imported part) of the core."""
    found = set()
    for file in CORE.rglob("*.py"):
        me = part(".".join(file.relative_to(CORE.parent).with_suffix("").parts))
        for node in ast.walk(ast.parse(file.read_text())):
            if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
                continue
            if node.module in ("mirrordash_core", "mirrordash_core.features"):  # from mirrordash_core.features import x
                targets = [part(f"{node.module}.{alias.name}") for alias in node.names]
            else:
                targets = [part(node.module)]
            found.update((me, target) for target in targets if me and target and target != me)
    return found


def test_no_new_imports_against_the_layers():
    found = imports()
    is_feature = lambda name: not name.startswith("/")
    mutual = {(a, b) for a, b in found if a < b and (b, a) in found and is_feature(a) and is_feature(b)}
    root_to_feature = {(a[1:], b) for a, b in found
                       if not is_feature(a) and is_feature(b) and a[1:] not in PUTS_THE_APP_TOGETHER}
    assert mutual - MUTUAL == set(), "two features import each other: move what they share down, or to the one that owns it"
    assert root_to_feature - ROOT_TO_FEATURE == set(), "shared code at the package root imports a feature"
    assert MUTUAL - mutual == set(), "no longer import each other: take them off MUTUAL"
    assert ROOT_TO_FEATURE - root_to_feature == set(), "no longer imports a feature: take it off ROOT_TO_FEATURE"
