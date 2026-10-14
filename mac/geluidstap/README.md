# Geluidstap (macOS)

Mini-app die het systeemgeluid aftapt met een Core Audio process tap
(macOS 14.2+), zodat LedFx zonder BlackHole naar de muziek kan luisteren.
Processen van Rogue Amoeba (SoundSource) worden uitgesloten, zodat de
analyse het geluid zonder EQ krijgt.

Bouwen: `mac/geluidstap/build.sh` → `~/Applications/Geluidstap.app`.
Een andere plek kan via `LICHTSHOW_GELUIDSTAP_APP=/pad/naar/Geluidstap.app`.

Bij de eerste start vraagt macOS om toestemming voor "systeemaudio
opnemen". Zonder die toestemming komt er geen geluid binnen.
