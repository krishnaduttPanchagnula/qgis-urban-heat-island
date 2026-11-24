"""Small self-checks for the pure parts of uhi_core (no QGIS/GDAL needed)."""
import os
import sys
import types

import numpy as np

gdal = types.ModuleType("osgeo.gdal")
gdal.GDT_Float32, gdal.GDT_Int16 = 6, 3
gdal.GRA_Bilinear, gdal.GRA_NearestNeighbour = 1, 0
osgeo = types.ModuleType("osgeo")
osgeo.gdal = gdal
osgeo.osr = types.ModuleType("osgeo.osr")
sys.modules.setdefault("osgeo", osgeo)
sys.modules.setdefault("osgeo.gdal", gdal)
sys.modules.setdefault("osgeo.osr", osgeo.osr)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "uhi_landsat"))
import uhi_core as core  # noqa: E402


def test_bbox():
    bbox = core.bbox_from_point(45.46, 9.19, 20)
    assert bbox[0] < 9.19 < bbox[2] and bbox[1] < 45.46 < bbox[3]
    assert (bbox[2] - bbox[0]) > (bbox[3] - bbox[1])  # degrees of longitude stretch


def test_brightness_temperature_increases_with_dn():
    cal = {"mult": 0.034, "add": 0.1, "k1": 774.8853, "k2": 1281.78}
    tb = core.brightness_temperature(np.array([100, 200, 300], dtype=np.float32), cal)
    assert np.all(np.diff(tb) > 0)
    assert np.all(np.isfinite(tb))


def test_split_window_amplifies_difference():
    tb10 = np.array([300.0, 300.0])
    tb11 = np.array([299.0, 298.0])
    result = core.split_window(tb10, tb11)
    assert np.all(result > tb10)
    assert result[1] > result[0]


def test_classify():
    classes = core.classify(np.array([19, 20.5, 21.5, 22.5, 24, np.nan]), [20, 21, 22, 23])
    assert list(classes[:5]) == [1, 2, 3, 4, 5]
    assert classes[5] == 0
    assert len(core.CLASS_LABELS) == 5


def test_vsi_path():
    href = ("https://landsatlook.usgs.gov/data/collection02/level-1/standard/"
            "oli-tirs/2023/054/216/SCENE/SCENE_B10.TIF")
    assert core._vsi_path(href).startswith("/vsis3/usgs-landsat/collection02/level-1/")


def test_source_order():
    assert core.source_order("auto", "/tmp/bands") == ["local", "aws_s3", "usgs_https"]
    assert core.source_order("auto", "") == ["aws_s3", "usgs_https"]
    assert core.source_order("aws_s3", "/tmp/bands") == ["aws_s3"]


def test_source_paths():
    item = {"assets": {k: {"href": "https://landsatlook.usgs.gov/data/x/SCENE_" + k + ".TIF"}
                       for k in ("lwir11", "lwir12", "qa_pixel", "MTL.json")}}
    assert core.source_paths("aws_s3", item)["b10"].startswith("/vsis3/usgs-landsat/x/")
    assert core.source_paths("usgs_https", item)["b11"].startswith("/vsicurl/https://")


def test_synthesise_l1_item():
    feature = {"id": "LC09_L2SP_193029_20230729_20230801_02_T1",
               "properties": {"datetime": "2023-07-29T10:04:36Z"}}
    item = core._synthesise_l1_item(feature)
    assert item["id"] == "LC09_L1GT_193029_20230729_20230729_02_T1"
    assert item["assets"]["lwir11"]["href"].endswith("_B10.TIF")


def test_new_scene_id_and_platform_case():
    import uhi_common

    item = core._synthesise_l1_item({
        "id": "LC09_L2SP_194029_20261101_02_T1",
        "properties": {"datetime": "2026-11-01T10:00:00Z"},
    })
    assert item["id"] == "LC09_L1GT_194029_20261101_20261101_02_T1"

    original = uhi_common._get_json
    uhi_common._get_json = lambda url: {"features": [{
        "id": "LC09_L1TP_194029_20261101_20261101_02_T1",
        "properties": {"platform": "LANDSAT_9", "landsat:cloud_cover_land": 5.0,
                       "datetime": "2026-11-01T10:00:00Z"},
    }]}
    try:
        candidates = uhi_common._query_catalog(
            "https://example.test", "landsat-c2l1", [9.0, 45.0, 9.5, 45.5],
            core._date("2026-11-01"), 7, "landsat:cloud_cover_land")
        assert len(candidates) == 1
    finally:
        uhi_common._get_json = original


def test_list_available_dates():
    import uhi_common

    def fake_get_json(url):
        if "landsatlook.usgs.gov" in url:
            features = [
                ("A", "LANDSAT_9", "landsat:cloud_cover_land", 20, "2026-10-24T10:00:00Z"),
                ("B", "LANDSAT_9", "landsat:cloud_cover_land", 80, "2026-10-24T11:00:00Z"),
                ("C", "LANDSAT_8", "landsat:cloud_cover_land", 10, "2026-10-20T10:00:00Z"),
            ]
        else:
            features = [
                ("D", "landsat-9", "eo:cloud_cover", 5, "2026-10-24T10:00:00Z"),
            ]
        return {"features": [{"id": i, "properties": {
            "platform": p, field: cloud, "datetime": when}}
            for i, p, field, cloud, when in features]}

    original = uhi_common._get_json
    uhi_common._get_json = fake_get_json
    try:
        dates = uhi_common.list_available_dates(
            45.0, 9.0, 20, uhi_common._date("2026-10-25"))
    finally:
        uhi_common._get_json = original
    assert [row[0] for row in dates] == ["2026-10-20", "2026-10-24"]
    assert dates[1][1] == 5  # clearest scene across both catalogues


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
