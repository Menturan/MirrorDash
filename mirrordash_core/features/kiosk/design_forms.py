# SPDX-License-Identifier: AGPL-3.0-only
"""The component library's settings section (/design#forms): every kind of setting, drawn by the admin form's
own code (forms.render_schema_form) from the schema printed beside it, so the picture and its JSON can't
drift apart. Only the words around them are written here."""

import json

from markupsafe import Markup

from mirrordash_core.forms import render_schema_form

LIST_METHOD = '''
# plugin.py: the choices, asked when the form opens and again when another field changes
async def settings_list_school(self, item):
    # item: the other fields of its list item (or the whole form); self.config: the unsaved form
    if not self.config.get("api_key"):
        raise ValueError("Enter the API key first")   # shown under the field
    data, error = await self.fetch_json(...)
    return [{"id": s["id"], "name": s["name"], "detail": s["town"]} for s in data]

# saved as {"id": "913", "name": "Fridhemsskolan"}'''

SEARCH_METHOD = '''
# plugin.py: asked as the owner types
async def settings_search_place(self, item, query):
    data, error = await self.fetch_json("https://api.example.com/places", params={"q": query})
    return [{"id": p["id"], "name": p["name"], "detail": p["region"]} for p in data or []]'''

LIST_ITEM_FIELDS = (
    "<p>A row's fields are drawn as the form's own: every kind of setting on this page works in a row, "
    "<code>show_if</code> (naming a field of the same row) and <code>group</code> too, and a <code>lan-url</code> "
    "in a row opens that address to the module. Files can't be in a row. A list or search choice gets the row's "
    "other fields. Rows fold to one line (the first field, the next one dimmed under it); a new one opens by itself.</p>")

# (name, tag, words above it or "", the settings, saved values, required keys, code after the JSON)
EXAMPLES = [
    ("Text Input", "type: string", "", {
        "city": {"type": "string", "title": "City", "description": "The city to show the weather for.", "default": "Stockholm"}},
     {}, [], ""),
    ("Toggle Switch", "type: boolean", "", {
        "show_seconds": {"type": "boolean", "title": "Show Seconds", "description": "Seconds in the clock.", "default": True}},
     {}, [], ""),
    ("Number Input", "type: integer / number", "", {
        "max_events": {"type": "integer", "title": "Max Events", "description": "Events shown at most.", "default": 10}},
     {}, [], ""),
    ("Range Slider", "type: integer / number  +  format: slider  +  minimum  +  maximum",
     "minimum and maximum alone are rules on a number box; format: slider makes it a slider between them.", {
        "volume": {"type": "integer", "format": "slider", "title": "Volume", "description": "How loud, 0–100.",
                   "minimum": 0, "maximum": 100, "default": 35}},
     {}, [], ""),
    ("Dropdown Select", "type: string  +  enum  (+ enum_titles)",
     "Without enum_titles an option shows its value with _ as a space and a capital first (open_meteo: Open meteo): "
     "write enum_titles for words people read.", {
         "language": {"type": "string", "title": "Language", "enum": ["sv", "en"], "enum_titles": ["Swedish", "English"],
                      "default": "sv"}},
     {}, [], ""),
    ("Shown Only for a Choice", "show_if: {\"<other key>\": [values]}  on any field",
     "Try it: pick Immich. The field shows only while the other one has one of the values (several keys: all must "
     "match). Hidden it keeps its saved value, so the module reads it whatever is picked. Point it at a dropdown or a "
     "text box of the same form, or a switch with [true] or [false] (not a field in a list item).", {
         "source": {"type": "string", "title": "Source", "enum": ["uploaded", "immich"],
                    "enum_titles": ["Photos uploaded here", "Immich"], "default": "uploaded"},
         "immich_url": {"type": "string", "format": "lan-url", "title": "Immich Address",
                        "show_if": {"source": ["immich"]}}},
     {}, [], ""),
    ("Shown Only While a Switch Is On", "show_if: {\"<switch key>\": [true]}",
     "Try it: turn Show Cover off. The same keyword on a switch: [true] while it's on, [false] while it's off.", {
         "show_cover": {"type": "boolean", "title": "Show Cover", "default": True},
         "cover_style": {"type": "string", "title": "Cover", "enum": ["gray", "color"],
                         "enum_titles": ["Black and white", "In colour"], "default": "gray",
                         "show_if": {"show_cover": [True]}}},
     {}, [], ""),
    ("Boxes to Tick", "type: array  +  items.type: string  +  items.enum  (also in a list item)", "", {
        "builtin_feeds": {"type": "array", "title": "Built-in Feeds", "description": "The news sources to show.",
                          "items": {"type": "string", "enum": ["bbc_news", "sr_ekot", "cnn_edition"],
                                    "enum_titles": ["BBC News", "SR Ekot", "CNN Edition"]},
                          "default": ["bbc_news"]}},
     {"builtin_feeds": ["bbc_news", "cnn_edition"]}, [], ""),
    ("List of Items", "type: array  +  items.type: object", LIST_ITEM_FIELDS, {
        "entities": {"type": "array", "title": "Entities", "description": "The devices to show.", "default": [],
                     "items": {"type": "object", "title": "Entity", "required": ["entity_id"], "properties": {
                         "entity_id": {"type": "string", "title": "Entity ID"},
                         "layout": {"type": "string", "title": "Layout", "enum": ["detailed", "compact"],
                                    "enum_titles": ["Detailed", "Compact"], "default": "compact"},
                         "readings": {"type": "array", "title": "Readings", "items": {
                             "type": "string", "enum": ["battery", "signal"], "enum_titles": ["Battery", "Signal"]}},
                         "custom_icon": {"type": "string", "format": "icon", "title": "Icon"},
                         "color": {"type": "string", "format": "color", "title": "Colour", "default": "#ffffff"},
                         "own_name": {"type": "boolean", "title": "Own Name", "default": False},
                         "custom_name": {"type": "string", "title": "Name", "show_if": {"own_name": [True]}}}}}},
     {"entities": [{"entity_id": "sensor.living_room", "layout": "detailed", "readings": ["battery"], "color": "#ffffff",
                    "own_name": True, "custom_name": "Living room"}, {}]}, [], ""),
    ("Nested Object (Accordion)", "type: object", "", {
        "advanced": {"type": "object", "title": "Advanced Settings", "description": "For those who need them.",
                     "properties": {"timeout": {"type": "integer", "title": "Timeout", "default": 30}}}},
     {}, [], ""),
    ("Settings in a Section", "\"group\": \"<title>\"  on each field  (saved flat, as without it)",
     "The fields with the same group share one section that opens with a click, where the first of them is. Only the "
     "form changes: the keys stay where they were, so prefer it to a nested object. It works with show_if.", {
         "timeout": {"type": "integer", "title": "Timeout (s)", "default": 30, "group": "Advanced"},
         "retries": {"type": "integer", "title": "Retries", "default": 3, "group": "Advanced"}},
     {}, [], ""),
    ("Colour Picker", "type: string  +  format: color  (the swatches, or any CSS colour typed)", "", {
        "accent_color": {"type": "string", "format": "color", "title": "Accent Colour", "default": "#ffffff"}},
     {}, [], ""),
    ("Password / API Key", "type: string  +  format: password  (mirrordash-sdk validate warns about a key like api_key without it)", "", {
        "api_key": {"type": "string", "format": "password", "title": "API Key", "description": "From the service.",
                    "default": ""}},
     {"api_key": "supersecretkey"}, [], ""),
    ("Textarea", "type: string  +  format: textarea", "", {
        "message": {"type": "string", "format": "textarea", "title": "Greeting", "default": "Good morning!"}},
     {}, [], ""),
    ("Icon", "type: string  +  format: icon  (a Lucide name, suggested while typing in the admin page)", "", {
        "icon": {"type": "string", "format": "icon", "title": "Icon", "description": "Shown before the value.",
                 "default": "thermometer-sun"}},
     {}, [], ""),
    ("Time", "type: string  +  format: time  (\"HH:MM\" or blank)", "", {
        "switch_time": {"type": "string", "format": "time", "title": "Switch to Tomorrow At", "default": "13:00"}},
     {}, [], ""),
    ("Home Network Address", "type: string  +  format: lan-url",
     "The module reaches the home network only at the addresses typed into these. The default is a hint, never filled in.", {
         "url": {"type": "string", "format": "lan-url", "title": "Home Assistant Address",
                 "default": "http://homeassistant.local:8123"}},
     {}, [], ""),
    ("Files", "format: files  +  accept: image  (no type, no default)",
     "Photos the owner uploads in the admin page, shrunk on the phone, kept in the module's cache folder (not in its "
     "settings). Not in a list item or a nested object.", {
         "photos": {"format": "files", "accept": "image", "max_files": 500, "title": "Photos"}},
     {}, [], ""),
    ("List (Choices From the Module)", "type: object  +  format: list  →  settings_list_<key>(self, item)",
     "For choices from outside the module (schools, calendars); choices known when the module is written are an "
     "<code>\"enum\"</code>. The key is a–z, 0–9 and _. A <code>ValueError</code>'s text shows under the field; the saved "
     "choice stays until another is picked. A module not saved yet answers too.", {
         "school": {"type": "object", "format": "list", "title": "School", "description": "Whose lunch is shown."}},
     {"school": {"id": "913", "name": "Fridhemsskolan"}}, [], LIST_METHOD),
    ("Search (Choices From the Module)", "type: object  +  format: search  →  settings_search_<key>(self, item, query)",
     "For choices too many to list (places, stations): suggestions open under the box as the owner types, picked with "
     "the mouse or the arrow keys and Enter. Answers and is saved as a list is.", {
         "place": {"type": "object", "format": "search", "title": "Place", "description": "Start typing to find it."}},
     {"place": {"id": "2711537", "name": "Gävle"}}, [], SEARCH_METHOD),
    ("Rules on What Is Typed", "required  ·  minimum / maximum  ·  minLength / maxLength  ·  pattern + pattern_message",
     "The admin page points at a broken rule before saving (opening a folded item that holds it), and the save "
     "refuses it. pattern matches the whole value; an empty value breaks only required. pattern_message is the "
     "only keyword of our own: the words for the owner.", {
         "url": {"type": "string", "title": "Calendar Address", "pattern": "https?://.+",
                 "pattern_message": "An address starting with http:// or https://"},
         "days": {"type": "integer", "title": "Days Ahead", "minimum": 1},
         "token": {"type": "string", "format": "password", "title": "Token", "minLength": 20}},
     {"url": "webcal://example"}, ["url"], ""),
]


def _code(properties: dict, required: list) -> str:
    """The settings as a module writes them in config_schema.json's "properties" (and its "required")."""
    parts = [f'"{key}": ' + json.dumps(prop, indent=2, ensure_ascii=False) for key, prop in properties.items()]
    if required:
        parts.append('"required": ' + json.dumps(required))
    return ",\n".join(parts)


def design_cards() -> list[dict]:
    """The cards of /design#forms: each example's words, its JSON and the form drawn from it."""
    return [{"name": name, "tag": tag, "desc": Markup(desc), "code": _code(properties, required) + extra,
             "form": Markup(render_schema_form({"properties": properties, "required": required}, values,
                                               "modules[demo]", "demo"))}
            for name, tag, desc, properties, values, required, extra in EXAMPLES]
