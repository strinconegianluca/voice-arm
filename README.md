# voice-arm

Controllo in italiano di un UR3e per la traccia Voice Control del Grok Hackathon. I comandi preparano un movimento e il braccio parte solo dopo una conferma. `ferma` lo arresta subito.

Senza un indirizzo IP il programma usa un UR3e simulato (6 giunti, sbraccio 500 mm). I comandi sono gli stessi che il controller vero riceve: `movej`, `movel`, `stopj`.

## Avvio

```powershell
py -3.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Apri `.env` e incolla una chiave API che inizia con `xai-`. Non condividere quel file: non è nel repository.

Comandi scritti, sul simulatore:

```powershell
.\.venv\Scripts\python.exe -m voice --text
```

Esempi: `solleva il vassoio`, `sì`, `vai a destra`, `indica il centro`, `spingi verso sinistra`, `appoggia`, `ferma`.

Microfono, con Grok Voice:

```powershell
.\.venv\Scripts\python.exe -m voice
```

La barra spaziatrice ferma il braccio. `Ctrl+C` chiude.

## URSim o braccio vero

In `.env`, `UR3E_IP` vuoto lascia il simulatore interno. Per URSim o per il controller del UR3e metti il suo IP. URSim in locale è `127.0.0.1`, con PolyScope in Remote Control. Lo stop software è `stopj`. L'arresto di sicurezza resta il pulsante del teach pendant.
