# SPDX-License-Identifier: AGPL-3.0-only
"""The schema of the Settings tab's form: the settings every module inherits ("globals")."""

from mirrordash_core.features.power.privacy import CARDS_OVER_PHOTO


async def get_globals_schema() -> dict:
    """Return the JSON schema defining global configuration settings."""
    import babel  # here, not at the top: its locale data is only needed by the admin page
    lang_list = sorted(((code, name) for code, name in babel.Locale('en').languages.items() if len(code) == 2),
                       key=lambda x: x[1])
    enum_codes = [code for code, name in lang_list]
    enum_titles = [name for code, name in lang_list]

    return {
        "title": "Global Settings",
        "description": "System-wide preferences inherited by all modules.",
        "properties": {
            "language": {
                "type": "string",
                "default": "en",
                "enum": enum_codes,
                "enum_titles": enum_titles,
                "title": "System Language",
                "description": "System translation language. Note: English is always used as a fallback if translations are missing."
            },
            "timezone": {
                "type": "string",
                "default": "Europe/Stockholm",
                "title": "Timezone",
                "description": "System timezone (e.g. Europe/Stockholm, America/New_York)."
            },
            "time_format": {
                "type": "string",
                "default": "24h",
                "enum": ["24h", "12h"],
                "title": "Clock Time Format",
                "description": "Global standard for clocks and times."
            },
            "temperature_unit": {
                "type": "string",
                "default": "C",
                "enum": ["C", "F"],
                "title": "Temperature Unit",
                "description": "Unit for thermometer and weather readouts."
            },
            "distance_unit": {
                "type": "string",
                "default": "km",
                "enum": ["km", "miles"],
                "title": "Distance Unit",
                "description": "Unit for travel, range, and maps."
            },
            "latitude": {
                "type": "number",
                "default": 59.3293,
                "title": "Decimal Latitude",
                "description": "Latitude coordinates for weather/astronomy."
            },
            "longitude": {
                "type": "number",
                "default": 18.0686,
                "title": "Decimal Longitude",
                "description": "Longitude coordinates for weather/astronomy."
            },
            "cards_over_photo": {
                "type": "string",
                "default": "see_through",
                "enum": list(CARDS_OVER_PHOTO),
                "enum_titles": ["See-through, the text shadowed", "A soft dark glow", "Half dark", "Dark, as without a photo"],
                "title": "Modules Over a Background Photo",
                "description": "How the modules look while a module (a photo) is in the Background position. Without one, a module's card is always dark, so cards that overlap cover each other."
            },
            "privacy_shows": {
                "type": "string",
                "default": "monogram",
                "enum": ["monogram", "nothing"],
                "enum_titles": ["The MirrorDash monogram", "Nothing: just the mirror"],
                "title": "Privacy Mode Without Modules",
                "description": "What the screen shows in privacy mode when no module is marked Shown in Privacy Mode. Turn privacy mode on below, with the button or from Home Assistant."
            },
            "safe_margin": {
                "type": "object",
                "title": "Screen Padding (Safe Margins)",
                "description": "Safe margin padding (in px) from physical screen edges.",
                "properties": {
                    "top": {
                        "type": "integer",
                        "default": 60,
                        "title": "Top Margin (px)",
                        "description": "Top padding in pixels."
                    },
                    "bottom": {
                        "type": "integer",
                        "default": 60,
                        "title": "Bottom Margin (px)",
                        "description": "Bottom padding in pixels."
                    },
                    "left": {
                        "type": "integer",
                        "default": 60,
                        "title": "Left Margin (px)",
                        "description": "Left padding in pixels."
                    },
                    "right": {
                        "type": "integer",
                        "default": 60,
                        "title": "Right Margin (px)",
                        "description": "Right padding in pixels."
                    }
                }
            }
        }
    }
