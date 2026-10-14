"""Tests voor HueDevice._hue_register met al opgeslagen credentials.

Achtergrond: met een geldige username antwoordt de Hue Bridge op
GET /api/<username> met een dict (de volledige bridge-config). Met een
ongeldige username antwoordt hij met een lijst [{"error": {...}}].
De oude code deed altijd response[0] en liep bij een dict vast met
KeyError: 0, waardoor Hue faalde na elke LedFx-herstart.
"""

from types import SimpleNamespace

import pytest

from ledfx.devices.hue import HueDevice


def _maak_device(monkeypatch, antwoord):
    """HueDevice zonder __init__ (die vereist mbedtls/netwerk), met nep-config."""
    device = HueDevice.__new__(HueDevice)
    config = SimpleNamespace(
        username="opgeslagen-user",
        clientkey="nep-clientkey",
        group_name="Lichtshow",
        ip_address="192.0.2.10",
    )
    monkeypatch.setattr(
        HueDevice, "config", property(lambda self: config), raising=False
    )
    aanroepen = {"requests": [], "updates": []}

    def nep_request(method, api_endpoint, data=None, ssl=False):
        aanroepen["requests"].append((method, api_endpoint))
        return antwoord, {}

    device._hue_request = nep_request
    device.update_config = lambda d: aanroepen["updates"].append(d)
    return device, aanroepen


def test_geldige_sleutel_geeft_geen_fout(monkeypatch):
    device, aanroepen = _maak_device(
        monkeypatch, {"config": {"name": "Hue Bridge"}, "lights": {}}
    )
    device._hue_register()
    assert aanroepen["requests"] == [("GET", "api/opgeslagen-user")]
    assert aanroepen["updates"] == []


def test_ingetrokken_sleutel_wist_en_meldt(monkeypatch):
    fout = [{"error": {"type": 1, "address": "/", "description": "unauthorized user"}}]
    device, aanroepen = _maak_device(monkeypatch, fout)
    with pytest.raises(Exception, match="press the Bridge Link Button and restart"):
        device._hue_register()
    assert aanroepen["updates"] == [{"username": None, "clientkey": None}]


def test_lege_lijst_crasht_niet(monkeypatch):
    device, aanroepen = _maak_device(monkeypatch, [])
    device._hue_register()
    assert aanroepen["updates"] == []


def test_lijst_zonder_error_is_geldig(monkeypatch):
    device, aanroepen = _maak_device(monkeypatch, [{"success": {}}])
    device._hue_register()
    assert aanroepen["updates"] == []
