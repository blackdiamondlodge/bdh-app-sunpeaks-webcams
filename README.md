# bdh-app-sunpeaks-webcams

Black Diamond Hub app: Sun Peaks resort webcam viewer. Moved out of
[BlackDiamondHub](https://github.com/blackdiamondlodge/BlackDiamondHub) and
installed into the hub through the `blackdiamondhub.plugins` entry point
(see [bdh-sdk](https://github.com/blackdiamondlodge/bdh-sdk)).

Page: `/sunpeaks_webcams/`. Cameras are grouped into elevation bands
(Alpine / Mid-mountain / Village); tap a tile for a full-size viewer.

## How updates work

The resort's cameras capture every 5 minutes, and the page shows a new
capture about 1-2 minutes later. `feed.py` keeps one shared copy of the
scraped page per process. It does nothing until the next capture is due
(newest timestamp + 5 min + 45 s), then polls with a conditional GET
(ETag / If-Modified-Since, so unchanged polls return an empty 304) every
20 s until the page changes. `get_webcams/` returns the cameras plus
`poll_after` seconds, so browsers poll only when there's something new
to fetch.

Tiles load `thumb/<slug>/<timestamp>.jpg`: a 1280 px JPEG (~60 KB)
rendered once per capture and served as immutable. The originals are
3-4K (up to ~2 MB) and load only in the viewer.

## Development

CI (`.github/workflows/ci.yml`) uses the reusable `app-ci.yml` from
bdh-sdk. It builds the wheel, installs it next to the hub, then runs
`hub_smoke`, the app tests and the selenium tests. CI also runs daily, because some tests hit the live site this app scrapes.

To release, bump `version` in `pyproject.toml`. When that lands on
`main`, `release.yml` tags it and publishes the wheel, and the release
notes include the hashed line for the hub's `sites/<site>/apps.txt`.
