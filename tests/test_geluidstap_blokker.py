"""Tests voor het omzetten van Geluidstap-bytes naar mono-blokken."""

import numpy as np

from ledfx.lichtshow.geluidstap import Blokker, lees_formaat


def _stereo_bytes(links, rechts):
    frames = np.column_stack([links, rechts]).astype(np.float32)
    return frames.tobytes()


def test_lees_formaat_vindt_regel():
    tekst = "uitgesloten: 1 Rogue Amoeba-proces(sen)\nklok: x\nformaat: 48000 Hz, 2 kanalen, float32\n"
    assert lees_formaat(tekst) == (48000, 2)


def test_lees_formaat_zonder_regel():
    assert lees_formaat("uitgesloten: 0\n") is None
    assert lees_formaat("") is None


def test_stereo_wordt_mono_gemiddelde():
    b = Blokker(kanalen=2, blokgrootte=4)
    blokken = b.voeg_toe(_stereo_bytes([1, 1, 0, 0], [0, 1, 0, -1]))
    assert len(blokken) == 1
    np.testing.assert_allclose(blokken[0], [0.5, 1.0, 0.0, -0.5])
    assert blokken[0].dtype == np.float32


def test_brokken_over_framegrens():
    links = np.arange(10, dtype=np.float32)
    rechts = np.arange(10, dtype=np.float32)
    data = _stereo_bytes(links, rechts)  # 80 bytes, 8 per frame
    b = Blokker(kanalen=2, blokgrootte=4)
    uit = []
    for stuk in (data[:3], data[3:13], data[13:41], data[41:]):  # willekeurige grenzen
        uit.extend(b.voeg_toe(stuk))
    assert [len(x) for x in uit] == [4, 4]
    np.testing.assert_allclose(np.concatenate(uit), np.arange(8))
    # de laatste 2 frames wachten op meer data
    meer = b.voeg_toe(_stereo_bytes([10, 11], [10, 11]))
    np.testing.assert_allclose(meer[0], [8, 9, 10, 11])


def test_lege_invoer():
    assert Blokker(kanalen=2, blokgrootte=4).voeg_toe(b"") == []
