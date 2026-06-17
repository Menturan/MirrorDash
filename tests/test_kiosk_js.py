import pytest
from fastapi.testclient import TestClient
from mirrordash_core.app import app

@pytest.fixture
def client():
    return TestClient(app)

def test_static_js_files_served(client):
    """Verify refactored JS files are served correctly."""
    # design-tokens.js
    resp = client.get('/static/js/kiosk/design-tokens.js')
    assert resp.status_code == 200
    assert 'DESIGN_TOKENS_CSS' in resp.text
    assert 'color-standard-gray' in resp.text

    # setup-prompt.js
    resp = client.get('/static/js/kiosk/setup-prompt.js')
    assert resp.status_code == 200
    assert 'customElements.define' in resp.text
    assert 'setup-prompt' in resp.text

    # core.js
    resp = client.get('/static/js/kiosk/core.js')
    assert resp.status_code == 200
    assert 'DESIGN_TOKENS_CSS' in resp.text
    assert 'WebSocket' in resp.text

    # lucide.min.js (local copy)
    resp = client.get('/static/js/lucide.min.js')
    assert resp.status_code == 200
    assert 'lucide' in resp.text

def test_index_html_uses_modular_js(client):
    """Verify index.html references split JS files, not inline script."""
    resp = client.get('/')
    assert resp.status_code == 200
    assert '/static/js/kiosk/design-tokens.js' in resp.text
    assert '/static/js/kiosk/setup-prompt.js' in resp.text
    assert '/static/js/kiosk/core.js' in resp.text
    assert '<setup-prompt>' in resp.text
    # Original inline script should be gone
    assert 'DESIGN_TOKENS_HTML' not in resp.text
    assert 'checkSystemOverlayPrompts' not in resp.text