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

## Telefono

All'avvio il programma stampa un indirizzo, per esempio `https://192.168.1.20:8765`. Aprilo dal telefono, sulla stessa rete Wi-Fi. Accetta il certificato: senza quello il browser non accende la fotocamera dal vivo. Se resta bloccata, la pagina ha un pulsante per scattare una foto.

Tieni il telefono sopra il tavolo, con il lato alto dello schermo verso il fondo, lontano dalla base del braccio. Poi, da testo o dal microfono: `punta verso la bottiglia`. Il programma manda l'ultimo fotogramma a Grok, che segna la pinza e l'oggetto. Lo spostamento nel video, sommato alla posizione vera del polso, diventa il bersaglio. Il braccio non si muove finché non confermi.

## URSim o braccio vero

In `.env`, `UR3E_IP` vuoto lascia il simulatore interno. Per URSim o per il controller del UR3e metti il suo IP. URSim in locale è `127.0.0.1`, con PolyScope in Remote Control. Lo stop software è `stopj`. L'arresto di sicurezza resta il pulsante del teach pendant.
