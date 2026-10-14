"""De Geluidstap-bron in AudioInputSource: lijst en openen."""

from unittest.mock import MagicMock, patch

from ledfx.effects import audio
from ledfx.effects.audio import AudioInputSource
from ledfx.lichtshow.geluidstap import GELUIDSTAP_DEVICE, GELUIDSTAP_HOSTAPI

NAAM = f"{GELUIDSTAP_HOSTAPI}: {GELUIDSTAP_DEVICE}"


def test_geen_app_geen_hostapi(monkeypatch):
    monkeypatch.setattr(audio, "geluidstap_beschikbaar", lambda: False)
    namen = [h["name"] for h in AudioInputSource.query_hostapis()]
    assert GELUIDSTAP_HOSTAPI not in namen
    assert NAAM not in AudioInputSource.input_devices().values()


def test_app_aanwezig_staat_in_lijst(monkeypatch):
    monkeypatch.setattr(audio, "geluidstap_beschikbaar", lambda: True)
    namen = [h["name"] for h in AudioInputSource.query_hostapis()]
    assert namen.count(GELUIDSTAP_HOSTAPI) == 1
    assert NAAM in AudioInputSource.input_devices().values()


def test_kiezen_opent_geluidstapstream(monkeypatch):
    from tests.test_audio_device_persistence import make_ais, make_mock_ledfx

    monkeypatch.setattr(audio, "geluidstap_beschikbaar", lambda: True)
    nep = MagicMock()
    monkeypatch.setattr(audio, "GeluidstapStream", nep)
    idx = {v: k for k, v in AudioInputSource.input_devices().items()}[NAAM]
    ledfx = make_mock_ledfx()
    ledfx.events = MagicMock()
    ais = make_ais(config={"audio_device": idx, "audio_device_name": NAAM}, ledfx=ledfx)
    ais._audio = MagicMock()
    ais._callbacks = []
    with patch.object(AudioInputSource, "_persist_active_device_config"):
        try:
            ais._activate_inner()
        finally:
            AudioInputSource._stream = None
            AudioInputSource._audio_stream_active = False
    nep.assert_called_once()
    assert nep.call_args.kwargs["blocks_per_second"] == ais._config.sample_rate
    nep.return_value.start.assert_called_once()
    ais._audio.InputStream.assert_not_called()
