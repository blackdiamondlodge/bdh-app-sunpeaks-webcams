"""The webcams as live backgrounds for other parts of the hub (e.g. the screensaver)."""
from bdh_sdk.provides import Background
from django.urls import reverse

REFRESH_SECONDS = 60


def build_backgrounds(cams):
    """One background per camera, Sunburst first; each URL always shows the newest capture."""
    cams = sorted((c for c in cams if c.get("slug")),
                  key=lambda c: "sunburst" not in (c.get("camera_name") or "").lower())
    return [Background(c["slug"], c.get("camera_name") or c["slug"],
                       reverse("webcam_latest", args=[c["slug"]]), refresh_seconds=REFRESH_SECONDS)
            for c in cams]


def backgrounds():
    from .views import feed

    cams, _wait = feed.get()
    return build_backgrounds(cams)
