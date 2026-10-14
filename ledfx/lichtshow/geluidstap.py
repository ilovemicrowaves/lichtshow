"""Geluidstap: systeemgeluid van de Mac als LedFx-audiobron.

Start de helper-app mac/geluidstap (Core Audio process tap) via `open`,
leest zijn ruwe float32-uitvoer uit een named pipe en levert mono-blokken
aan LedFx' _audio_sample_callback, net als WebAudioStream en
SendspinAudioStream.
"""

import logging
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

_LOGGER = logging.getLogger(__name__)

_FORMAAT = re.compile(r"formaat:\s*(\d+)\s*Hz,\s*(\d+)\s*kanalen")


def lees_formaat(tekst: str) -> tuple[int, int] | None:
    """Haal (rate, kanalen) uit de stderr-tekst van de app, of None."""
    m = _FORMAAT.search(tekst)
    return (int(m.group(1)), int(m.group(2))) if m else None


class Blokker:
    """Zet ruwe float32 interleaved bytes om in mono-blokken van vaste lengte."""

    def __init__(self, kanalen: int, blokgrootte: int):
        self.kanalen = kanalen
        self.blokgrootte = blokgrootte
        self._bytes = b""
        self._mono = np.zeros(0, dtype=np.float32)

    def voeg_toe(self, data: bytes) -> list[np.ndarray]:
        framebytes = 4 * self.kanalen
        buf = self._bytes + data
        heel = len(buf) - len(buf) % framebytes
        self._bytes = buf[heel:]
        if heel:
            frames = np.frombuffer(buf[:heel], dtype=np.float32).reshape(
                -1, self.kanalen
            )
            mono = frames.mean(axis=1, dtype=np.float32)
            self._mono = np.concatenate([self._mono, mono])
        n = len(self._mono) // self.blokgrootte
        blokken = [
            self._mono[i * self.blokgrootte : (i + 1) * self.blokgrootte].copy()
            for i in range(n)
        ]
        self._mono = self._mono[n * self.blokgrootte :]
        return blokken


GELUIDSTAP_HOSTAPI = "MAC SYSTEM AUDIO"
GELUIDSTAP_DEVICE = "Geluidstap"
GEEN_TOESTEMMING_MELDING = (
    "Geluidstap hears no audio. Allow it in System Settings → Privacy & "
    "Security → Screen & System Audio Recording (Systeeminstellingen → "
    "Privacy en beveiliging → Opname van scherm en systeemaudio), then "
    "select the audio source again."
)


def app_pad() -> Path:
    eigen = os.environ.get("LICHTSHOW_GELUIDSTAP_APP")
    return Path(eigen) if eigen else Path.home() / "Applications" / "Geluidstap.app"


def geluidstap_beschikbaar() -> bool:
    return sys.platform == "darwin" and app_pad().is_dir()


def start_app(fifo: str, errbestand: str) -> Callable[[], None]:
    """Start Geluidstap.app via `open` (nodig voor de audiotoestemming)."""
    app = app_pad()
    binair = str(app / "Contents" / "MacOS" / "geluidstap")
    subprocess.run(["pkill", "-f", binair], check=False)  # wees van een vorige run
    subprocess.run(
        ["open", "-n", "-a", str(app), "--stdout", fifo, "--stderr", errbestand],
        check=True,
    )
    return lambda: subprocess.run(["pkill", "-f", binair], check=False)


class GeluidstapStream:
    """Audiobron met hetzelfde contract als WebAudioStream/SendspinAudioStream."""

    def __init__(
        self,
        callback,
        blocks_per_second: int,
        on_error=None,
        starter=None,
        wachttijd: float = 5.0,
        stiltijd: float = 3.0,
    ):
        self.callback = callback
        self.blocks_per_second = blocks_per_second
        self.on_error = on_error
        self.starter = starter or start_app
        self.wachttijd = wachttijd
        self.stiltijd = stiltijd
        self._actief = False
        self._map = None
        self._stopper = None
        self._lezer = None
        self._gemeld = False
        self._laatste_data = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._map = tempfile.mkdtemp(prefix="lichtshow-tap-")
        self._fifo = os.path.join(self._map, "tap.fifo")
        self._err = os.path.join(self._map, "tap.err")
        os.mkfifo(self._fifo)
        # Niet-blokkerend openen: lukt meteen, ook als de app nog niet schrijft.
        # Zo kan stop() de leesthread altijd beëindigen.
        self._fd = os.open(self._fifo, os.O_RDONLY | os.O_NONBLOCK)
        self._actief = True
        self._gemeld = False
        self._laatste_data = None
        try:
            self._stopper = self.starter(self._fifo, self._err)
        except Exception as fout:
            self._actief = False
            os.close(self._fd)
            shutil.rmtree(self._map, ignore_errors=True)
            self._map = None
            raise OSError(f"Geluidstap could not be started: {fout}") from fout
        self._lezer = threading.Thread(
            target=self._lees, name="geluidstap", daemon=True
        )
        self._lezer.start()
        threading.Thread(
            target=self._waakhond, name="geluidstap-waakhond", daemon=True
        ).start()

    def _meld(self, melding: str) -> None:
        with self._lock:
            if self._gemeld or not self._actief:
                return
            self._gemeld = True
        _LOGGER.warning("Geluidstap: %s", melding)
        if self.on_error:
            try:
                self.on_error(melding)
            except Exception:
                _LOGGER.exception("on_error faalde")

    def _err_tekst(self) -> str:
        try:
            with open(self._err) as f:
                return f.read()
        except OSError:
            return ""

    def _waakhond(self) -> None:
        eind = time.monotonic() + self.wachttijd
        while self._actief and self._laatste_data is None and time.monotonic() < eind:
            time.sleep(0.05)
        if self._actief and self._laatste_data is None:
            fout = [
                r for r in self._err_tekst().splitlines() if r.startswith("geluidstap:")
            ]
            self._meld(fout[0] if fout else GEEN_TOESTEMMING_MELDING)
            return
        # Daarna blijven opletten: stilte in de muziek levert nog steeds
        # (nul-)buffers op; géén buffers betekent dat de bron is gestokt,
        # bijvoorbeeld omdat het uitvoerapparaat (de klok) wegviel.
        while self._actief:
            time.sleep(0.1)
            laatste = self._laatste_data
            if laatste is not None and time.monotonic() - laatste > self.stiltijd:
                self._meld(
                    "Geluidstap receives no audio anymore (output device gone?)."
                )
                return

    def _lees(self) -> None:
        fd = self._fd
        blokker = None
        try:
            while self._actief:
                klaar, _, _ = select.select([fd], [], [], 0.2)
                if not klaar:
                    continue
                try:
                    data = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                if not data:
                    if blokker is None:
                        # Nog geen schrijver verbonden: een lege fifo meldt EOF.
                        time.sleep(0.05)
                        continue
                    self._meld("Geluidstap stopped unexpectedly (gestopt).")
                    return
                if blokker is None:
                    formaat = lees_formaat(self._err_tekst())
                    if formaat is None:
                        self._meld("Geluidstap sent audio without a format line.")
                        return
                    rate, kanalen = formaat
                    blokker = Blokker(
                        kanalen, max(1, round(rate / self.blocks_per_second))
                    )
                self._laatste_data = time.monotonic()
                for blok in blokker.voeg_toe(data):
                    if not self._actief:
                        return
                    try:
                        self.callback(blok, len(blok), None, None)
                    except Exception:
                        _LOGGER.exception("Fout in LedFx-audiocallback")
        finally:
            os.close(fd)

    def stop(self) -> None:
        if not self._actief and self._map is None:
            return
        self._actief = False
        if self._stopper:
            self._stopper()
            self._stopper = None
        if self._lezer:
            self._lezer.join(timeout=2.0)
            self._lezer = None
        if self._map:
            shutil.rmtree(self._map, ignore_errors=True)
            self._map = None

    def close(self) -> None:
        self.stop()
