from bdh_sdk import PluginSpec, Tile
from bdh_sdk.provides import BACKGROUNDS

spec = PluginSpec(
    id="sunpeaks_webcams",
    app="sunpeaks_webcams",
    urls="sunpeaks_webcams.urls",
    provides={BACKGROUNDS: "sunpeaks_webcams.provides.backgrounds"},
    tiles=(
        Tile(
            title="Webcams",
            url_name="webcams",
            icon="sunpeaks_webcams/hub/icon.svg",
            section="mountain",
            subtitle="Live mountain views",
            order=30,
        ),
    ),
)
