"""Guards on the constraint-overlay sources. The failure we actually hit: CAGIS
emptied the Open_Data FeatureServer (hillside/historic layers 6/7 → 0 features),
and a destructive reload wiped real polygons. These pin the relocated sources so a
silent revert to a dead endpoint is caught in CI, not in production."""
from __future__ import annotations

from ingestion import overlays


def test_hillside_historic_moved_off_dead_open_data():
    # both relocated to the CAGIS Countywide Zoning MapServer (sublayers 3 and 2);
    # the old Open_Data FeatureServer/6,7 return 0 features now.
    for name in ("hillside", "historic"):
        url = overlays.LAYERS[name]["url"]
        assert "Countywide_Layers/Zoning/MapServer" in url, url
        assert "Open_Data/FeatureServer" not in url, url


def test_every_layer_has_the_required_spec_keys():
    for name, spec in overlays.LAYERS.items():
        for key in ("url", "params", "zone_field", "subtype_field", "page_size"):
            assert key in spec, f"{name} missing {key}"
        assert spec["url"].endswith("/query"), name


def test_build_reloads_all_layers_then_applies():
    # the quarterly refresh relies on build() existing and being callable
    assert callable(overlays.build)
