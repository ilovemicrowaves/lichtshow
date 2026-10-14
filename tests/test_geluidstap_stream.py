"""Tests voor GeluidstapStream met een nep-bron in plaats van de Mac-app."""

import os
import threading
import time

import numpy as np

from ledfx.lichtshow.geluidstap import GeluidstapStream


def _nep_starter(frames=4800, formaat=True, na_data_stoppen=False, schrijven=True):
    """Doet wat de app doet: formaatregel naar stderr, float32 stereo naar de fifo."""
    stop = threading.Event()

    def starter(fifo, errbestand):
        with open(errbestand, "w") as f:
            if formaat:
                f.write("formaat: 48000 Hz, 2 kanalen, float32\n")

        def schrijf():
            if not schrijven:
                stop.wait()
                return
            with open(fifo, "wb") as f:
                data = np.full((frames, 2), 0.25, dtype=np.float32).tobytes()
                f.write(data)
                f.flush()
                if not na_data_stoppen:
                    stop.wait()

        threading.Thread(target=schrijf, daemon=True).start()
        return stop.set

    return starter


def _wacht(voorwaarde, s=3.0):
    eind = time.monotonic() + s
    while time.monotonic() < eind:
        if voorwaarde():
            return True
        time.sleep(0.02)
    return False


def test_levert_blokken_van_juiste_lengte():
    blokken, fouten = [], []
    st = GeluidstapStream(
        lambda d, n, t, s: blokken.append(d),
        blocks_per_second=60,
        on_error=fouten.append,
        starter=_nep_starter(frames=4800),
    )
    st.start()
    assert _wacht(lambda: len(blokken) >= 6)
    st.stop()
    assert all(len(b) == 800 for b in blokken)  # 48000 / 60
    np.testing.assert_allclose(blokken[0], 0.25)
    assert fouten == []


def test_geen_data_geeft_melding():
    fouten = []
    st = GeluidstapStream(
        lambda *a: None,
        blocks_per_second=60,
        on_error=fouten.append,
        starter=_nep_starter(schrijven=False),
        wachttijd=0.5,
    )
    st.start()
    assert _wacht(lambda: fouten)
    st.stop()
    assert "Systeeminstellingen" in fouten[0]
    assert len(fouten) == 1


def test_app_stopt_geeft_melding():
    fouten = []
    st = GeluidstapStream(
        lambda *a: None,
        blocks_per_second=60,
        on_error=fouten.append,
        starter=_nep_starter(frames=800, na_data_stoppen=True),
    )
    st.start()
    assert _wacht(lambda: fouten)
    st.stop()
    assert "gestopt" in fouten[0]


def test_stop_voor_verbinding_hangt_niet():
    st = GeluidstapStream(
        lambda *a: None,
        blocks_per_second=60,
        starter=_nep_starter(schrijven=False),
    )
    st.start()
    tmp = st._map
    t0 = time.monotonic()
    st.stop()
    assert time.monotonic() - t0 < 3.0
    assert not os.path.exists(tmp)
    st.close()  # mag nogmaals, zonder fout


def test_geen_melding_na_stop():
    fouten = []
    st = GeluidstapStream(
        lambda *a: None,
        blocks_per_second=60,
        on_error=fouten.append,
        starter=_nep_starter(frames=800),
        wachttijd=0.5,
    )
    st.start()
    time.sleep(0.2)
    st.stop()
    time.sleep(0.8)
    assert fouten == []
