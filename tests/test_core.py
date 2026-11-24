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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
