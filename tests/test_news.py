import pytest
import json
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from mirrordash_news.plugin import NewsModule, strip_html, parse_feed_xml, fetch_feed

MOCK_RSS_DATA = """<?xml version="1.0" encoding="utf-8" ?>
<rss version="2.0">
<channel>
    <title>Mock News Source</title>
    <description>Latest mock news</description>
    <item>
        <title>Headline 1 &amp; SVT</title>
        <description>&lt;p&gt;Preamble 1 with HTML tags.&lt;/p&gt;</description>
        <pubDate>Sun, 07 Jun 2026 12:00:00 GMT</pubDate>
    </item>
    <item>
        <title>Headline 2</title>
        <description>Preamble 2 without tags</description>
    </item>
</channel>
</rss>
"""

MOCK_ATOM_DATA = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
    <title>Mock Atom Feed</title>
    <entry>
        <title>Atom Headline 1</title>
        <summary>Atom summary 1</summary>
    </entry>
    <entry>
        <title>Atom Headline 2</title>
        <content type="html">Atom content &lt;b&gt;2&lt;/b&gt;</content>
    </entry>
</feed>
"""

def test_strip_html():
    assert strip_html("<p>Hello <b>World</b></p>") == "Hello World"
    assert strip_html("Headline &amp; Preamble") == "Headline & Preamble"
    assert strip_html("   Spaced   Text   ") == "Spaced Text"
    assert strip_html("") == ""
    assert strip_html(None) == ""

def test_parse_feed_xml_rss():
    items = parse_feed_xml(MOCK_RSS_DATA.encode("utf-8"))
    assert len(items) == 2
    assert items[0]["title"] == "Headline 1 & SVT"
    assert items[0]["preamble"] == "Preamble 1 with HTML tags."
    assert items[1]["title"] == "Headline 2"
    assert items[1]["preamble"] == "Preamble 2 without tags"

def test_parse_feed_xml_atom():
    items = parse_feed_xml(MOCK_ATOM_DATA.encode("utf-8"))
    assert len(items) == 2
    assert items[0]["title"] == "Atom Headline 1"
    assert items[0]["preamble"] == "Atom summary 1"
    assert items[1]["title"] == "Atom Headline 2"
    assert items[1]["preamble"] == "Atom content 2"

@patch("urllib.request.urlopen")
def test_fetch_feed_success(mock_urlopen):
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = MOCK_RSS_DATA.encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response

    items = fetch_feed("http://localhost/news.xml")
    assert len(items) == 2
    assert items[0]["title"] == "Headline 1 & SVT"

@patch("urllib.request.urlopen")
def test_fetch_feed_failure(mock_urlopen):
    mock_response = MagicMock()
    mock_response.status = 404
    mock_urlopen.return_value.__enter__.return_value = mock_response

    items = fetch_feed("http://localhost/news.xml")
    assert len(items) == 0

@pytest.mark.asyncio
@patch("urllib.request.urlopen")
async def test_fetch_all_feeds_mapping(mock_urlopen):
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = MOCK_RSS_DATA.encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response

    config = {
        "feeds": [
            {"name": "Mock Source", "url": "http://localhost/rss.xml"}
        ],
        "max_items": 3
    }
    
    module = NewsModule(config)
    items = await module.fetch_all_feeds(config["feeds"])
    
    assert len(items) == 2
    assert items[0]["source"] == "Mock Source"
    assert items[0]["title"] == "Headline 1 & SVT"

@pytest.mark.asyncio
async def test_run_loop_missing_feeds():
    config = {
        "feeds": []
    }
    module = NewsModule(config)
    module.render_template = MagicMock(return_value="<div>No Feeds</div>")
    
    broadcast_func = AsyncMock()
    
    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        try:
            await module.run_loop(broadcast_func)
        except asyncio.CancelledError:
            pass
            
    broadcast_func.assert_called_once()
    args = broadcast_func.call_args[0]
    assert "No Feeds" in args[1]
    module.render_template.assert_called_with(
        "widget.html",
        error="No feeds configured",
        items=[]
    )
