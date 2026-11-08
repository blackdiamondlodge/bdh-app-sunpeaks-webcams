from bdh_sdk import PluginSpec, Tile

spec = PluginSpec(
    id="sunpeaks_webcams",
    app="sunpeaks_webcams",
    urls="sunpeaks_webcams.urls",
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
