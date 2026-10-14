"""Geluidstap: systeemgeluid van de Mac als LedFx-audiobron.

Start de helper-app mac/geluidstap (Core Audio process tap) via `open`,
leest zijn ruwe float32-uitvoer uit een named pipe en levert mono-blokken
aan LedFx' _audio_sample_callback, net als WebAudioStream en
SendspinAudioStream.
"""

import re

import numpy as np

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
