"""Landsat 8/9 band 10 + 11 -> land surface temperature and UHI classes.

Discovery tries two public STAC catalogues. Band access tries several sources in
order (local folder, AWS S3 requester-pays, USGS HTTPS with an optional session
cookie). Rasters are processed in 512-row blocks and written straight to disk, so
peak memory stays a few MB regardless of the requested radius.
"""

import json
import math
import os
import tempfile
from datetime import datetime, timezone

import numpy as np
from osgeo import gdal, osr

try:  # packaged (QGIS plugin / pip wheel)
    from .uhi_common import (
        BLOCK_ROWS, CLASS_LABELS, S3_BUCKET, SOURCE_LABELS, SW_A, SW_B, SW_C, Cancelled,
        _date, _flatten, _synthesise_l1_item, bbox_from_point,
        brightness_temperature, classify, class_edges, find_scene, source_order,
        split_window,
    )
except ImportError:  # flat import from a source checkout
    from uhi_common import (
        BLOCK_ROWS, CLASS_LABELS, S3_BUCKET, SOURCE_LABELS, SW_A, SW_B, SW_C, Cancelled,
        _date, _flatten, _synthesise_l1_item, bbox_from_point,
        brightness_temperature, classify, class_edges, find_scene, source_order,
        split_window,
    )


def _vsi_path(href):
    if href.startswith("s3://"):
        return "/vsis3/" + href[5:]
    if "/data/" in href:
        return "/vsis3/{}/{}".format(S3_BUCKET, href.split("/data/", 1)[1])
    return "/vsicurl/" + href


def _local_path(local_dir, href):
    wanted = os.path.basename(href).upper()
    for root, _, files in os.walk(local_dir):
        for name in files:
            if name.upper() == wanted:
                return os.path.join(root, name)
    raise RuntimeError("The selected folder does not contain {}".format(os.path.basename(href)))


def source_paths(source, item, local_dir=None):
    """Resolve one source into GDAL paths for B10, B11, QA and MTL.json."""
    assets = item["assets"]
    keys = {"b10": "lwir11", "b11": "lwir12", "qa": "qa_pixel", "mtl": "MTL.json"}
    paths = {}
    for name, key in keys.items():
        if key not in assets:
            raise RuntimeError("STAC item has no {} asset".format(key))
        href = assets[key]["href"]
        if source == "local":
            if not local_dir:
                raise RuntimeError("No local folder selected")
            paths[name] = _local_path(local_dir, href)
        elif source == "aws_s3":
            paths[name] = _vsi_path(href)
        elif source == "usgs_https":
            paths[name] = "/vsicurl/" + href
        else:
            raise RuntimeError("Unknown source {}".format(source))
    return paths


def _prepare_source(source, cookie):
    gdal.SetConfigOption("GDAL_HTTP_TIMEOUT", "30")
    gdal.SetConfigOption("GDAL_HTTP_MAX_RETRY", "1")
    if source == "aws_s3":
        gdal.SetConfigOption("AWS_REQUEST_PAYER", "requester")
        if not (os.environ.get("AWS_ACCESS_KEY_ID")
                or os.path.isfile(os.path.expanduser("~/.aws/credentials"))):
            raise RuntimeError("No AWS account details found. Add them or choose another option.")
    else:
        gdal.SetConfigOption("AWS_REQUEST_PAYER", "")
    if cookie:
        gdal.SetConfigOption("GDAL_HTTP_HEADERS", "Cookie: " + cookie)
    else:
        gdal.SetConfigOption("GDAL_HTTP_HEADERS", "")


def open_source(source, item, local_dir, cookie):
    """Try a source and return (paths, datasets) or raise with a short reason."""
    _prepare_source(source, cookie)
    paths = source_paths(source, item, local_dir)
    datasets = [gdal.Open(paths[key]) for key in ("b10", "b11", "qa")]
    if any(ds is None for ds in datasets):
        raise RuntimeError("satellite images could not be opened")
    _read_text(paths["mtl"])  # fail here rather than halfway through the blocks
    return paths, datasets


def resolve_source(preferred, item, local_dir, cookie):
    attempts = []
    for source in source_order(preferred, local_dir):
        try:
            paths, datasets = open_source(source, item, local_dir, cookie)
            return source, paths, datasets, attempts
        except Exception as exc:
            attempts.append("{}: {}".format(SOURCE_LABELS.get(source, source), exc))
    raise RuntimeError("Could not get the satellite images. " + " | ".join(attempts))


def _read_text(path):
    if path.startswith("/vsi"):
        handle = gdal.VSIFOpenL(path, "rb")
        if handle is None:
            raise RuntimeError("could not read " + os.path.basename(path))
        gdal.VSIFSeekL(handle, 0, 2)
        size = gdal.VSIFTellL(handle)
        gdal.VSIFSeekL(handle, 0, 0)
        data = b""
        while len(data) < size:
            chunk = gdal.VSIFReadL(1, min(65536, size - len(data)), handle)
            if not chunk:
                break
            data += chunk
        gdal.VSIFCloseL(handle)
        return data.decode("utf-8", "replace")
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def load_calibration(mtl_path):
    fields = _flatten(json.loads(_read_text(mtl_path)), {})
    cal = {}
    for band in (10, 11):
        try:
            cal[band] = {
                "mult": float(fields["RADIANCE_MULT_BAND_{}".format(band)]),
                "add": float(fields["RADIANCE_ADD_BAND_{}".format(band)]),
                "k1": float(fields["K1_CONSTANT_BAND_{}".format(band)]),
                "k2": float(fields["K2_CONSTANT_BAND_{}".format(band)]),
            }
        except KeyError as exc:
            raise RuntimeError("MTL.json is missing {}".format(exc.args[0]))
    return cal


def _window(dataset, bbox):
    source = osr.SpatialReference()
    source.ImportFromWkt(dataset.GetProjectionRef())
    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    transform = osr.CoordinateTransformation(wgs84, source)
    x1, y1, _ = transform.TransformPoint(bbox[0], bbox[1], 0)
    x2, y2, _ = transform.TransformPoint(bbox[2], bbox[3], 0)
    gt = dataset.GetGeoTransform()
    minx, maxx = min(x1, x2), max(x1, x2)
    miny, maxy = min(y1, y2), max(y1, y2)
    off_x = max(0, int(math.floor((minx - gt[0]) / gt[1])))
    off_y = max(0, int(math.floor((gt[3] - maxy) / abs(gt[5]))))
    width = int(math.ceil((maxx - minx) / abs(gt[1])))
    height = int(math.ceil((maxy - miny) / abs(gt[5])))
    width = min(width, dataset.RasterXSize - off_x)
    height = min(height, dataset.RasterYSize - off_y)
    if width <= 0 or height <= 0:
        raise RuntimeError("Your area is outside the available satellite image. "
                           "Try a smaller radius or another date.")
    out_gt = (gt[0] + off_x * gt[1], gt[1], gt[2],
              gt[3] + off_y * gt[5], gt[4], gt[5])
    return off_x, off_y, width, height, out_gt, dataset.GetProjectionRef()


def _create(path, width, height, geotransform, projection, dtype, nodata):
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(path, width, height, 1, dtype,
                            ["COMPRESS=LZW", "TILED=YES"])
    dataset.SetGeoTransform(geotransform)
    dataset.SetProjection(projection)
    dataset.GetRasterBand(1).SetNoDataValue(nodata)
    return dataset


def _set_metadata(dataset, metadata):
    for key, value in metadata.items():
        dataset.SetMetadataItem(key, str(value))


def compute(params, progress=None, cancel=None):
    """Fetch one scene and stream LST + UHI rasters to disk. Returns a result dict."""
    def step(value, message):
        if progress:
            progress(value, message)

    if cancel and cancel():
        raise Cancelled()

    step(2, "Searching Landsat catalogues")
    bbox, item = find_scene(params["lat"], params["lon"], params["radius_km"],
                            _date(params["day"]))

    step(8, "Opening bands")
    source, paths, datasets, attempts = resolve_source(
        params.get("source", "auto"), item, params.get("local_dir") or None,
        params.get("cookie") or os.environ.get("USGS_COOKIE", ""))
    b10, b11, qa = datasets
    cal = load_calibration(paths["mtl"])

    off_x, off_y, width, height, geotransform, projection = _window(b10, bbox)
    step(12, "Computing temperature ({} x {} pixels)".format(width, height))

    label = "".join(c if c.isalnum() or c in "-_" else "_"
                    for c in params.get("location") or "area")
    stamp = _date(params["day"]).isoformat()
    output_dir = params["output_dir"]
    os.makedirs(output_dir, exist_ok=True)
    lst_path = os.path.join(output_dir, "LST_{}_{}.tif".format(label, stamp))
    uhi_path = os.path.join(output_dir, "UHI_{}_{}.tif".format(label, stamp))

    lst_ds = _create(lst_path, width, height, geotransform, projection,
                     gdal.GDT_Float32, -9999.0)
    lst_band = lst_ds.GetRasterBand(1)
    count = 0
    total = 0.0
    total_sq = 0.0
    minimum, maximum = math.inf, -math.inf

    try:
        for y in range(0, height, BLOCK_ROWS):
            rows = min(BLOCK_ROWS, height - y)
            if cancel and cancel():
                raise Cancelled()
            dn10 = b10.GetRasterBand(1).ReadAsArray(off_x, off_y + y, width, rows)
            dn11 = b11.GetRasterBand(1).ReadAsArray(off_x, off_y + y, width, rows)
            qa_values = qa.GetRasterBand(1).ReadAsArray(off_x, off_y + y, width, rows)
            tb10 = brightness_temperature(dn10, cal[10])
            tb11 = brightness_temperature(dn11, cal[11])
            celsius = split_window(tb10, tb11) - 273.15
            clear = ((qa_values >> 6) & 1).astype(bool)
            water = ((qa_values >> 7) & 1).astype(bool)
            valid = clear & ~water & np.isfinite(celsius) & (celsius >= -30) & (celsius <= 80)
            values = celsius[valid]
            lst_band.WriteArray(np.where(valid, celsius, -9999.0).astype(np.float32), 0, y)
            if values.size:
                count += int(values.size)
                total += float(values.sum())
                total_sq += float((values ** 2).sum())
                minimum = min(minimum, float(values.min()))
                maximum = max(maximum, float(values.max()))
            step(12 + int(72 * (y + rows) / height), "Processing row {}/{}".format(y + rows, height))
        if not count:
            raise RuntimeError("The image is too cloudy to get a result. Try another date.")
        mean = total / count
        spread = math.sqrt(max(0.0, total_sq / count - mean * mean))
        if spread < 0.1:
            spread = 1.0
        edges = [mean - 0.5 * spread, mean, mean + 0.5 * spread, mean + 1.5 * spread]
    except Exception:
        lst_ds = None
        if os.path.exists(lst_path):
            os.remove(lst_path)
        raise
    finally:
        lst_ds = None

    step(88, "Writing UHI classes")
    uhi_ds = _create(uhi_path, width, height, geotransform, projection,
                     gdal.GDT_Byte, 0)
    uhi_band = uhi_ds.GetRasterBand(1)
    try:
        lst_ds = gdal.Open(lst_path)
        for y in range(0, height, BLOCK_ROWS):
            rows = min(BLOCK_ROWS, height - y)
            if cancel and cancel():
                raise Cancelled()
            values = lst_ds.GetRasterBand(1).ReadAsArray(0, y, width, rows)
            values = np.where(values <= -9990, np.nan, values)
            uhi_band.WriteArray(classify(values, edges), 0, y)
    except Exception:
        if os.path.exists(uhi_path):
            os.remove(uhi_path)
        raise
    finally:
        lst_ds = None
        uhi_ds = None

    provenance = {
        "SOURCE_USED": source,
        "SOURCES_TRIED": " | ".join(attempts) or source,
        "CATALOG": "USGS LandsatLook / Element84 Earth Search",
        "SCENE_ID": item.get("id", ""),
        "ACQUIRED": item["properties"].get("datetime", ""),
        "B10": paths["b10"],
        "B11": paths["b11"],
        "RETRIEVED_UTC": datetime.now(timezone.utc).isoformat(),
        "LICENSE": "USGS Landsat data policy (public domain)",
        "ALGORITHM": "LST = T10 + A*(T10-T11) + B*(T10-T11)^2 + C; "
                     "A={}, B={}, C={}".format(SW_A, SW_B, SW_C),
    }
    for path in (lst_path, uhi_path):
        dataset = gdal.Open(path, gdal.GA_Update)
        _set_metadata(dataset, provenance)
        dataset = None

    step(100, "Done")
    return {
        "lst_path": lst_path, "uhi_path": uhi_path,
        "stats": {"mean": mean, "min": minimum, "max": maximum,
                  "valid_pixels": count, "edges": edges},
        "source": source, "attempts": attempts, "item": item,
    }
