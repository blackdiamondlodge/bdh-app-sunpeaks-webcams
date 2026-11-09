import logging

import requests
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from .feed import PAGE_URL, STALE_AFTER, TIMEOUT, WebcamFeed, group_by_elevation, parse_page

log = logging.getLogger(__name__)

feed = WebcamFeed()

# Clients re-poll shortly after the feed's next upstream check, bounded so a
# skewed clock can't park them for long.
POLL_SLACK = 3
POLL_MIN = 10
POLL_MAX = 6 * 60


def webcams(request):
    cams, wait = feed.get()
    context = {
        'bands': group_by_elevation(cams),
        'cam_count': len(cams),
        'stale_after': STALE_AFTER,
        'poll_after': _poll_after(wait),
    }
    return render(request, 'sunpeaks_webcams/webcams.html', context)


def _poll_after(wait):
    return int(min(POLL_MAX, max(POLL_MIN, wait + POLL_SLACK)))


@never_cache
def check_for_new_webcams_json(request):
    cams, wait = feed.get()
    return JsonResponse({'webcams': cams, 'poll_after': _poll_after(wait)})


def webcam_thumbnail(request, slug, timestamp):
    try:
        data = feed.thumbnail(slug, timestamp)
    except Exception:
        log.exception('Thumbnail for %s/%s failed', slug, timestamp)
        url = feed.image_url(slug, timestamp)
        if url is None:
            raise Http404
        return HttpResponseRedirect(url)
    if data is None:
        raise Http404
    resp = HttpResponse(data, content_type='image/jpeg')
    resp['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp


def check_for_new_webcams():
    """Scrape the resort page directly, bypassing the shared feed."""
    response = requests.get(PAGE_URL, timeout=TIMEOUT)
    response.raise_for_status()
    return parse_page(response.content)