"""Smoke + worker test for the pip-only desktop app.

Needs only pip packages (PySide6, rasterio, numpy) - no Tk, no system GDAL.
Run with: QT_QPA_PLATFORM=offscreen python3 tests/test_desktop.py
"""
import json
import math
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desktop_app"))

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from rasterio.warp import transform_bounds  # noqa: E402

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import uhi_desktop  # noqa: E402

WIDTH = HEIGHT = 1200
TRANSFORM = from_origin(500000.0, 5050000.0, 30.0, 30.0)
CAL = {
    10: {"mult": 0.034, "add": 0.1, "k1": 774.8853, "k2": 1281.78},
    11: {"mult": 0.034, "add": 0.1, "k1": 729.5417, "k2": 1304.4138},
}


def dn_for(kelvin, band):
    c = CAL[band]
    return (c["k1"] / math.expm1(c["k2"] / kelvin) - c["add"]) / c["mult"]


def make_tiff(path, array):
    with rasterio.open(path, "w", driver="GTiff", height=HEIGHT, width=WIDTH,
                       count=1, dtype="uint16", crs="EPSG:32632",
                       transform=TRANSFORM) as dst:
        dst.write(array, 1)


def build_scene(folder):
    os.makedirs(folder, exist_ok=True)
    base = np.full((HEIGHT, WIDTH), dn_for(303.15, 10), dtype=np.float32)
    base[400:550, 400:550] = dn_for(311.15, 10)
    base[650:800, 650:800] = dn_for(297.15, 10)
    make_tiff(os.path.join(folder, "TEST_B10.TIF"), base.astype(np.uint16))
    b11 = np.full((HEIGHT, WIDTH), dn_for(303.15, 11), dtype=np.float32)
    b11[400:550, 400:550] = dn_for(311.15, 11)
    b11[650:800, 650:800] = dn_for(297.15, 11)
    make_tiff(os.path.join(folder, "TEST_B11.TIF"), b11.astype(np.uint16))

    qa = np.full((HEIGHT, WIDTH), 1 << 6, dtype=np.uint16)
    qa[500:520, 430:460] = 1 << 3
    qa[700:720, 700:730] = (1 << 6) | (1 << 7)
    make_tiff(os.path.join(folder, "TEST_QA_PIXEL.TIF"), qa)

    mtl = {"LANDSAT_METADATA_FILE": {"RADIOMETRIC_RESCALING": {}, "TIRS_THERMAL_CONSTANTS": {}}}
    for band in (10, 11):
        for key, name in (("mult", "RADIANCE_MULT_BAND_{}"),
                          ("add", "RADIANCE_ADD_BAND_{}")):
            mtl["LANDSAT_METADATA_FILE"]["RADIOMETRIC_RESCALING"][name.format(band)] = CAL[band][key]
        for key, name in (("k1", "K1_CONSTANT_BAND_{}"), ("k2", "K2_CONSTANT_BAND_{}")):
            mtl["LANDSAT_METADATA_FILE"]["TIRS_THERMAL_CONSTANTS"][name.format(band)] = CAL[band][key]
    with open(os.path.join(folder, "TEST_MTL.json"), "w") as handle:
        json.dump(mtl, handle)

    def href(name):
        return "https://landsatlook.usgs.gov/data/collection02/x/TEST_" + name

    item = {
        "id": "TEST_L1",
        "properties": {"datetime": "2023-07-29T10:00:00Z", "landsat:cloud_cover_land": 1.0},
        "assets": {"lwir11": {"href": href("B10.TIF")}, "lwir12": {"href": href("B11.TIF")},
                   "qa_pixel": {"href": href("QA_PIXEL.TIF")}, "MTL.json": {"href": href("MTL.json")}},
        "links": [],
    }
    bbox = transform_bounds(
        "EPSG:32632", "EPSG:4326",
        TRANSFORM.c + 150 * 30, TRANSFORM.f + 950 * -30,
        TRANSFORM.c + 950 * 30, TRANSFORM.f + 150 * -30)
    return folder, item, list(bbox)


def test_source_paths():
    item = {"assets": {k: {"href": "https://landsatlook.usgs.gov/data/x/SCENE_" + k}
                       for k in ("lwir11", "lwir12", "qa_pixel", "MTL.json")}}
    paths = uhi_desktop.core.source_paths("aws_s3", item)
    assert paths["b10"] == "s3://usgs-landsat/x/SCENE_lwir11"
    assert uhi_desktop.core.source_paths("usgs_https", item)["b11"].startswith("https://")


def _qapp():
    return QApplication.instance() or QApplication([])


def test_desktop_worker():
    _qapp()
    window = QWidget()
    app = uhi_desktop.App(window)
    try:
        assert app._params()["source"] == "auto"
        assert app._params()["radius_km"] == 20.0

        with tempfile.TemporaryDirectory() as tmp:
            bands, item, bbox = build_scene(os.path.join(tmp, "bands"))
            original = uhi_desktop.core.find_scene
            uhi_desktop.core.find_scene = lambda *a, **k: (bbox, item)
            try:
                params = {
                    "day": "2023-07-29", "location": "TestCity",
                    "lat": (bbox[1] + bbox[3]) / 2, "lon": (bbox[0] + bbox[2]) / 2,
                    "radius_km": 20, "output_dir": os.path.join(tmp, "out"),
                    "local_dir": bands, "source": "local", "cookie": "",
                }
                app._work(params)
            finally:
                uhi_desktop.core.find_scene = original

            messages = []
            while True:
                try:
                    messages.append(app.queue.get_nowait())
                except Exception:
                    break
            kinds = [m[0] for m in messages]
            assert "done" in kinds, messages
            result = [m[1] for m in messages if m[0] == "done"][0]
            assert result["stats"]["valid_pixels"] > 500000
            assert 28 < result["stats"]["mean"] < 32
            assert os.path.exists(result["lst_path"])
            assert any(m[0] == "progress" and m[1] == 100 for m in messages)
            with rasterio.open(result["lst_path"]) as lst:
                assert lst.tags()["SOURCE_USED"] == "local"
                assert lst.width > uhi_desktop.core.BLOCK_ROWS
            print("ok desktop_worker: {} messages, mean {:.2f} C".format(
                len(messages), result["stats"]["mean"]))
    finally:
        window.close()


if __name__ == "__main__":
    test_source_paths()
    test_desktop_worker()
