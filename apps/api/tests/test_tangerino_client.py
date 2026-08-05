"""Testes do adapter Tangerino (ponto/Solides)."""
from __future__ import annotations

from app.core.config import Settings


def test_settings_defaults_tangerino_e_onvio():
    s = Settings(_env_file=None)
    assert s.tangerino_api_key is None
    assert s.tangerino_base_url == "https://employer.tangerino.com.br"
    assert s.onvio_client_id is None
    assert s.onvio_client_secret is None
    assert s.onvio_integration_key is None
    assert s.onvio_audience == "409f91f6-dc17-44c8-a5d8-e0a1bafd8b67"
    assert s.onvio_allow_send is False
