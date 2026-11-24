"""Headless end-to-end test of the streaming compute path.

Needs real GDAL bindings (e.g. conda-forge gdal). Run with:
    python tests/test_integration.py
"""
import json
import math
import os
import sys
import tempfile

import numpy as np
from osgeo import gdal, osr

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "uhi_landsat"))
import uhi_core as core  # noqa: E402

WIDTH = HEIGHT = 1200
GT = (500000.0, 30.0, 0.0, 5050000.0, 0.0, -30.0)
CAL = {
    10: {"mult": 0.034, "add": 0.1, "k1": 774.8853, "k2": 1281.78},
    11: {"mult": 0.034, "add": 0.1, "k1": 729.5417, "k2": 1304.4138},
}


def dn_for(kelvin, band):
    c = CAL[band]
    radiance = c["k1"] / math.expm1(c["k2"] / kelvin)
    return (radiance - c["add"]) / c["mult"]


def make_tiff(path, array, dtype=gdal.GDT_UInt16, nodata=None):
    ds = gdal.GetDriverByName("GTiff").Create(path, WIDTH, HEIGHT, 1, dtype)
    ds.SetGeoTransform(GT)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32632)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.WriteArray(array)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    ds = None


def build_scene(folder):
    os.makedirs(folder, exist_ok=True)
    base = np.full((HEIGHT, WIDTH), dn_for(303.15, 10), dtype=np.float32)
    # warm block and cool block, both well inside the requested window
    base[400:550, 400:550] = dn_for(311.15, 10)
    base[650:800, 650:800] = dn_for(297.15, 10)
    make_tiff(os.path.join(folder, "TEST_B10.TIF"), base.astype(np.uint16))
    b11 = np.full((HEIGHT, WIDTH), dn_for(303.15, 11), dtype=np.float32)
    b11[400:550, 400:550] = dn_for(311.15, 11)
    b11[650:800, 650:800] = dn_for(297.15, 11)
    make_tiff(os.path.join(folder, "TEST_B11.TIF"), b11.astype(np.uint16))

    qa = np.full((HEIGHT, WIDTH), 1 << 6, dtype=np.uint16)  # clear
    qa[500:520, 430:460] = 1 << 3                          # cloud, not clear
    qa[700:720, 700:730] = (1 << 6) | (1 << 7)             # clear but water
    make_tiff(os.path.join(folder, "TEST_QA_PIXEL.TIF"), qa, gdal.GDT_UInt16)

    mtl = {"LANDSAT_METADATA_FILE": {"RADIOMETRIC_RESCALING": {}, "TIRS_THERMAL_CONSTANTS": {}}}
    for band in (10, 11):
        for key in ("mult", "add"):
            name = "RADIANCE_MULT_BAND_{}".format(band) if key == "mult" else "RADIANCE_ADD_BAND_{}".format(band)
            mtl["LANDSAT_METADATA_FILE"]["RADIOMETRIC_RESCALING"][name] = CAL[band][key]
        for key in ("k1", "k2"):
            name = "K{}_CONSTANT_BAND_{}".format(1 if key == "k1" else 2, band)
            mtl["LANDSAT_METADATA_FILE"]["TIRS_THERMAL_CONSTANTS"][name] = CAL[band][key]
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

    # window covering pixels [150:950, 150:950] in UTM, expressed as WGS84 bbox
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32632)
    wgs = osr.SpatialReference()
    wgs.ImportFromEPSG(4326)
    to_wgs = osr.CoordinateTransformation(srs, wgs)
    x1 = GT[0] + 150 * GT[1]
    x2 = GT[0] + 950 * GT[1]
    y1 = GT[3] + 950 * GT[5]
    y2 = GT[3] + 150 * GT[5]
    lon1, lat1, _ = to_wgs.TransformPoint(x1, y1, 0)
    lon2, lat2, _ = to_wgs.TransformPoint(x2, y2, 0)
    return folder, item, [min(lon1, lon2), min(lat1, lat2), max(lon1, lon2), max(lat1, lat2)]


def test_end_to_end():
    original_find = core.find_scene
    with tempfile.TemporaryDirectory() as root:
        bands, item, bbox = build_scene(os.path.join(root, "bands"))
        core.find_scene = lambda *a, **k: (bbox, item)
        try:
            result = core.compute({
                "day": "2023-07-29", "location": "TestCity", "lat": (bbox[1] + bbox[3]) / 2,
                "lon": (bbox[0] + bbox[2]) / 2, "radius_km": 20,
                "output_dir": os.path.join(root, "out"), "local_dir": bands,
                "source": "local", "cookie": "",
            })
        finally:
            core.find_scene = original_find

        assert result["source"] == "local"
        lst = gdal.Open(result["lst_path"])
        uhi = gdal.Open(result["uhi_path"])
        assert lst is not None and uhi is not None
        assert abs(lst.RasterXSize - 800) <= 2, lst.RasterXSize
        assert abs(lst.RasterYSize - 800) <= 2, lst.RasterYSize
        assert lst.RasterYSize > core.BLOCK_ROWS, "test must span multiple blocks"

        values = lst.GetRasterBand(1).ReadAsArray()
        classes = uhi.GetRasterBand(1).ReadAsArray()
        assert values.shape == classes.shape
        valid = values > -9990
        assert valid.sum() > 500000
        assert 28 < result["stats"]["mean"] < 32, result["stats"]["mean"]
        assert set(np.unique(classes)).issubset(set(range(6)))
        assert 1 in np.unique(classes) and 5 in np.unique(classes)
        assert lst.GetMetadataItem("SOURCE_USED") == "local"
        assert lst.GetMetadataItem("SCENE_ID") == "TEST_L1"
        # cloud and water pixels must be nodata in LST and class 0 in UHI
        assert np.all(classes[values <= -9990] == 0)
        print("ok end_to_end: {}x{}, mean {:.2f} C, {} valid pixels".format(
            lst.RasterXSize, lst.RasterYSize, result["stats"]["mean"],
            result["stats"]["valid_pixels"]))


def test_source_fallback():
    bands, item, _ = build_scene(os.path.join(tempfile.mkdtemp(), "bands"))
    original = core.open_source

    def flaky(source, got_item, local_dir, cookie):
        if source == "aws_s3":
            raise RuntimeError("simulated AWS failure")
        return original("local", got_item, bands, cookie)

    core.open_source = flaky
    try:
        source, paths, datasets, attempts = core.resolve_source("auto", item, "", "")
    finally:
        core.open_source = original
    assert source == "usgs_https"  # the source whose attempt succeeded
    assert any("AWS account" in a for a in attempts), attempts
    assert paths["b10"].endswith("TEST_B10.TIF")
    for ds in datasets:
        ds = None
    print("ok source_fallback: tried {} then used {}".format(attempts, source))


if __name__ == "__main__":
    import resource
    test_end_to_end()
    test_source_fallback()
    print("peak RSS: {:.1f} MB".format(
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024))
