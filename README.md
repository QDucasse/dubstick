# dubstick

Scan a vinyl record from your phone, get each track's BPM and Camelot key, and print them on a
StickEasy / Nelko P21 label for the sleeve.

- Identify a release on Discogs by barcode (phone camera), catalog number or free text.
- Each track is analysed with Essentia on the best full-length audio available — your local
  file, a recording of the record playing, or the YouTube video linked on Discogs — and
  cross-checked against its 30-second Deezer preview. Disagreements and low-confidence
  results are flagged for review.
- Review and override values; everything is kept in a library searchable by BPM range and key.
- Print one label per record over Bluetooth LE, with a preview.

## Running locally

```sh
uv sync
cp .env.example .env           # then fill it in
uv run --env-file .env dubstick   # http://localhost:8000
```

`DISCOGS_TOKEN` comes from discogs.com → Settings → Developers.

| Variable | Default | |
|---|---|---|
| `DUBSTICK_DATA` | `./data` | SQLite database and audio cache |
| `DUBSTICK_MUSIC` | – | colon-separated music folders, read only |
| `DUBSTICK_ESSENTIA_WORKERS` | `2` | parallel analysis processes |
| `DUBSTICK_PORT` | `8000` | |
| `DUBSTICK_PRINTER` | – | printer address for `dubstick-printer`, found with its `scan` command |

`ffmpeg` must be on the `PATH`: it decodes all audio before analysis.

## Printer

```sh
uv run dubstick-printer scan
uv run --env-file .env dubstick-printer status
uv run dubstick-printer preview "A:140/8A,138/9A B:132/7A,140/-" -o label.png
uv run --env-file .env dubstick-printer print "A:140/8A,138/9A B:132/7A,140/-"
```

The printer only talks over BLE (its USB port only charges). Each job connects, checks the loaded
roll and the printer state, prints, and disconnects, so the phone app keeps working too.

## Deployment

The container talks to the host's BlueZ over D-Bus. Docker's default AppArmor profile blocks that,
so load the bundled profile once on the host:

```sh
sudo apparmor_parser -r -W deploy/apparmor/dubstick-ble
```

Then copy the service from `deploy/compose.yml` into your stack, or run it on its own:

```sh
docker compose --env-file .env -f deploy/compose.yml up -d --build
```

It expects Traefik with an `authelia` middleware in front; `DUBSTICK_HOST` is the public hostname.
Inside the container, music folders are mounted under `/music`.

## Tests

```sh
uv run pytest
```
