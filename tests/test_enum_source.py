"""items.enum_source "installed_modules" (ARCHITECTURE #68): filled where a schema is read, drawn, dropped on save."""

import copy
from types import SimpleNamespace
from unittest.mock import patch

from mirrordash_core.features.modules import schema as schema_module
from mirrordash_core.forms import cast_values_by_schema, render_schema_form

MINE = {"properties": {"allowed": {"type": "array", "title": "Allowed", "default": [],
                                   "items": {"type": "string", "enum_source": "installed_modules"}}}}
EPS = [SimpleNamespace(name=n) for n in ("mirrordash_zeta", "mirrordash-heads-up", "mirrordash_alpha", "mirrordash_plain")]
SCHEMAS = {"mirrordash_zeta": {"title": "Alpha Clock"}, "mirrordash-heads-up": None,
           "mirrordash_alpha": {"title": "Zed"}, "mirrordash_plain": None}


def read(ep, also_in=None):
    if ep.name == "mirrordash-heads-up":
        return {"title": "Heads-up", **copy.deepcopy(MINE)}
    return SCHEMAS[ep.name]


def filled(eps=EPS):
    with patch("mirrordash_core.features.modules.module_files.module_entry_points", return_value=eps), \
         patch.object(schema_module, "_read_schema", side_effect=read):
        return schema_module.get_module_schema(next(ep for ep in EPS if ep.name == "mirrordash-heads-up"))


def test_the_enum_is_the_installed_modules_but_itself_sorted_by_title():
    items = filled()["properties"]["allowed"]["items"]
    assert items["enum"] == ["mirrordash_zeta", "mirrordash_plain", "mirrordash_alpha"]
    assert items["enum_titles"] == ["Alpha Clock", "Plain", "Zed"]


def test_the_form_draws_a_box_per_module_and_ticks_the_saved_ones():
    html = render_schema_form(filled(), {"allowed": ["mirrordash_alpha"]})
    assert html.count('type="checkbox"') == 3 and "Alpha Clock" in html
    assert html.count("checked") == 1 and "No other modules" not in html


def test_no_other_module_says_so():
    only_me = [ep for ep in EPS if ep.name == "mirrordash-heads-up"]
    html = render_schema_form(filled(only_me), {})
    assert "No other modules are installed." in html and 'type="checkbox"' not in html


def test_a_value_of_an_uninstalled_module_is_dropped_on_save():
    saved = cast_values_by_schema({"allowed": ["", "mirrordash_alpha", "mirrordash_gone"]}, filled())
    assert saved == {"allowed": ["mirrordash_alpha"]}
