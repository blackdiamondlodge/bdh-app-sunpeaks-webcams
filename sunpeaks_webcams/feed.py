"""Schedule-aware, shared cache of the Sun Peaks webcam page.

The resort's cameras capture every 5 minutes and the page shows a new capture
roughly 1-2 minutes later. Rather than scraping on every page load / client
poll, a single process-wide feed:

* sleeps until the next capture is due (newest timestamp + interval + lag),
* then polls with a conditional GET (ETag / If-Modified-Since -> 304, no body)
  every ``RETRY_INTERVAL`` seconds until the page changes,
* and serves downscaled thumbnails, rendered once per capture.
"""
import io
import logging
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import parse_qs, urlsplit

import requests
from bs4 import BeautifulSoup
from django.utils.text import slugify

log = logging.getLogger(__name__)

PAGE_URL = 'https://www.sunpeaksresort.com/bike-hike/weather-webcams/webcams'
DOMAIN = 'https://www.sunpeaksresort.com'

CAPTURE_INTERVAL = 300   # cameras capture every 5 minutes
PUBLISH_LAG = 45         # first check this long after the next capture is due
RETRY_INTERVAL = 20      # poll cadence while an update is due
LATE_INTERVAL = 60       # cadence once an update is a whole interval overdue
ERROR_INTERVAL = 60
TIMEOUT = 10
STALE_AFTER = 15 * 60

THUMB_WIDTH = 1280
THUMB_QUALITY = 80
THUMB_CACHE_SIZE = 40

# (label, minimum elevation in metres), highest first.
BANDS = (('Alpine', 1900), ('Mid-mountain', 1400), ('Village', 0))


def _elevation_m(text):
    m = re.search(r'([\d,]+)\s*m\b', text or '')
    return int(m.group(1).replace(',', '')) if m else None


def _time_label(text):
    m = re.search(r'(\d{1,2}:\d{2})\s*([ap]m)', text or '', re.I)
    return f'{m.group(1)} {m.group(2).upper()}' if m else ''


def parse_page(html):
    """Parse the resort's webcam page into a list of camera dicts."""
    soup = BeautifulSoup(html, 'html.parser')
    if not soup.find('div', {'id': 'webcams'}):
        return []

    cams, slugs = [], set()
    for cam in soup.find_all('div', class_='cam'):
        try:
            name = cam.find('h2').text.strip()
            image_url = cam.find('div', class_='image').find('img')['src']
            rows = cam.find('table').find_all('tr')
        except (AttributeError, KeyError, TypeError):
            log.warning('Skipping malformed webcam block')
            continue
        if image_url.startswith('/'):
            image_url = DOMAIN + image_url

        def cell(i):
            tds = rows[i].find_all('td') if len(rows) > i else []
            return tds[1].text.strip() if len(tds) > 1 else ''

        timestamp = parse_qs(urlsplit(image_url).query).get('timestamp', [None])[0]
        if timestamp is not None and not timestamp.isdigit():
            timestamp = None

        slug = base = slugify(name) or 'cam'
        n = 2
        while slug in slugs:
            slug, n = f'{base}-{n}', n + 1
        slugs.add(slug)

        elevation = cell(2)
        metres = _elevation_m(elevation)
        cams.append({
            'camera_name': name,
            'slug': slug,
            'image_url': image_url,
            'timestamp': timestamp,
            'last_updated': cell(0),
            'time_label': _time_label(cell(0)),
            'location': cell(1),
            'elevation': elevation,
            'elevation_m': metres,
            'elevation_label': f'{metres:,} m' if metres is not None else '',
        })
    return cams


def group_by_elevation(cams):
    """Group cameras into elevation bands (highest first), dropping empty ones."""
    groups = [{'name': name, 'cams': []} for name, _ in BANDS]
    other = {'name': 'Other', 'cams': []}
    for cam in cams:
        metres = cam.get('elevation_m')
        if metres is None:
            other['cams'].append(cam)
            continue
        for group, (_, floor) in zip(groups, BANDS):
            if metres >= floor:
                group['cams'].append(cam)
                break

    bands = []
    for group in groups + [other]:
        if not group['cams']:
            continue
        group['cams'].sort(key=lambda c: -(c.get('elevation_m') or 0))
        heights = sorted({c['elevation_m'] for c in group['cams'] if c.get('elevation_m') is not None})
        if len(heights) == 1:
            group['range'] = f'{heights[0]:,} m'
        elif heights:
            group['range'] = f'{heights[0]:,} \u2013 {heights[-1]:,} m'
        else:
            group['range'] = ''
        n = len(group['cams'])
        group['cols'] = n if n <= 4 else -(-n // 2)
        rows = -(-n // group['cols'])
        # Rows of 4 narrow tiles get a little extra height to stay close to 16:9.
        group['weight'] = rows * (1.25 if group['cols'] >= 4 else 1)
        bands.append(group)
    return bands


def make_thumbnail(data, width=THUMB_WIDTH):
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im = im.convert('RGB')
        im.thumbnail((width, width * 4))
        out = io.BytesIO()
        im.save(out, 'JPEG', quality=THUMB_QUALITY, optimize=True, progressive=True)
    return out.getvalue()


class WebcamFeed:
    def __init__(self, session=None, clock=time.time):
        self._session = session or requests.Session()
        self._clock = clock
        self._lock = threading.Lock()
        self._cams = None
        self._etag = None
        self._last_modified = None
        self._next_check = 0.0
        self._thumb_lock = threading.Lock()
        self._thumbs = OrderedDict()

    def latest_capture(self):
        stamps = [int(c['timestamp']) for c in self._cams or [] if c.get('timestamp')]
        return max(stamps) if stamps else None

    def _schedule(self, now):
        latest = self.latest_capture()
        if latest is None:
            return now + ERROR_INTERVAL
        due = latest + CAPTURE_INTERVAL + PUBLISH_LAG
        if now < due:
            return due
        if now - due > CAPTURE_INTERVAL:
            return now + LATE_INTERVAL
        return now + RETRY_INTERVAL

    def _refresh(self, now):
        headers = {}
        if self._cams:
            if self._etag:
                headers['If-None-Match'] = self._etag
            if self._last_modified:
                headers['If-Modified-Since'] = self._last_modified
        try:
            resp = self._session.get(PAGE_URL, headers=headers, timeout=TIMEOUT)
            if resp.status_code != 304:
                resp.raise_for_status()
                cams = parse_page(resp.content)
                if cams:
                    self._cams = cams
                    self._etag = resp.headers.get('ETag')
                    self._last_modified = resp.headers.get('Last-Modified')
                else:
                    log.warning('Sun Peaks webcam page had no cameras')
            if self._cams is None:
                self._cams = []
            self._next_check = self._schedule(now)
        except requests.RequestException as exc:
            log.warning('Sun Peaks webcam fetch failed: %s', exc)
            if self._cams is None:
                self._cams = []
            self._next_check = now + ERROR_INTERVAL

    def get(self):
        """Return (cameras, seconds until the feed will next look for an update)."""
        with self._lock:
            now = self._clock()
            if self._cams is None or now >= self._next_check:
                self._refresh(now)
            return list(self._cams), max(0.0, self._next_check - now)

    def image_url(self, slug, timestamp):
        cams, _ = self.get()
        cam = next((c for c in cams if c['slug'] == slug), None)
        if cam is None:
            return None
        return f"{cam['image_url'].split('?', 1)[0]}?timestamp={timestamp}"

    def thumbnail(self, slug, timestamp):
        """Downscaled JPEG bytes for a camera capture, or None for an unknown camera."""
        key = (slug, str(timestamp))
        with self._thumb_lock:
            if key in self._thumbs:
                self._thumbs.move_to_end(key)
                return self._thumbs[key]

        url = self.image_url(slug, timestamp)
        if url is None:
            return None
        resp = self._session.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = make_thumbnail(resp.content)

        with self._thumb_lock:
            self._thumbs[key] = data
            while len(self._thumbs) > THUMB_CACHE_SIZE:
                self._thumbs.popitem(last=False)
        return data
