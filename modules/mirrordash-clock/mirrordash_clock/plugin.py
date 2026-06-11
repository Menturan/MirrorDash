import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from babel.dates import format_date as babel_format_date

logger = logging.getLogger("mirrordash.modules.clock")

def format_localized_date(now, date_format: str, lang: str) -> str:
    try:
        formatted = babel_format_date(now, format=date_format, locale=lang)
        if formatted:
            return formatted[0].upper() + formatted[1:]
        return formatted
    except Exception as e:
        logger.warning(f"Babel format failed for locale '{lang}' with format '{date_format}': {e}. Using fallback.")
        return now.strftime("%A, %B %d, %Y")

class ClockModule:
    def __init__(self, config):
        self.config = config
        self.name = "mirrordash_clock"
        global_cfg = config.get("globals", {})
        
        # Fallback format: check instance config first, then global config
        self.format = config.get("format") or global_cfg.get("time_format", "24h")
        self.show_seconds = config.get("show_seconds", True)
        self.show_header = config.get("show_header", True)
        
        # Localization and custom date layout
        self.lang = global_cfg.get("language", "en")
        self.date_format = config.get("date_format", "full")
        
        # Timezone configuration
        self.timezone_name = global_cfg.get("timezone", "Europe/Stockholm")
        try:
            self.tz = ZoneInfo(self.timezone_name)
            logger.info("ClockModule timezone set to %s", self.timezone_name)
        except Exception as e:
            self.tz = None
            logger.warning("Invalid timezone '%s' in globals: %s. Using local system time.", self.timezone_name, e)
            
        logger.info("Initializing ClockModule with format: %s, show_seconds: %s, lang: %s, date_format: %s", 
                    self.format, self.show_seconds, self.lang, self.date_format)

    async def run_loop(self, broadcast_func):
        logger.info("Starting ClockModule run loop")
        try:
            while True:
                now = datetime.now(self.tz) if self.tz else datetime.now()
                
                if self.format == "12h":
                    hm_str = now.strftime("%I:%M")
                    ampm_str = now.strftime("%p")
                else:
                    hm_str = now.strftime("%H:%M")
                    ampm_str = ""

                s_str = now.strftime(":%S") if self.show_seconds else ""
                date_str = format_localized_date(now, self.date_format, self.lang)

                html = self.render_template(
                    "clock.html",
                    hm_str=hm_str,
                    s_str=s_str,
                    ampm_str=ampm_str,
                    date_str=date_str,
                    show_header=self.show_header
                )

                await broadcast_func(self.name, html)

                # Sleep until the exact next whole second to prevent drift
                next_second = (now + timedelta(seconds=1)).replace(microsecond=0)
                current_now = datetime.now(self.tz) if self.tz else datetime.now()
                sleep_secs = (next_second - current_now).total_seconds()
                await asyncio.sleep(max(0, sleep_secs))
        except asyncio.CancelledError:
            logger.info("Clock module stopped.")
            raise

