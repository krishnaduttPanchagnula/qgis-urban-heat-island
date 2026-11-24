"""pip-friendly engine for the desktop app.

Same maths as the QGIS plugin's uhi_core, but rasterio wheels bundle GDAL, so
there is no system library or conda dependency. The QGIS plugin keeps using
osgeo/GDAL and is untouched.
"""

import json
import math
import os
from contextlib import ExitStack
from datetime import datetime, timezone
from urllib.request import Request, urlopen

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import Window

try:  # pip-installed package
    from uhi_landsat.uhi_common import (
        BLOCK_ROWS, S3_BUCKET, SOURCE_LABELS, SW_A, SW_B, SW_C, Cancelled,
        _date, _flatten, bbox_from_point, brightness_temperature, classify,
        find_scene, list_available_dates, source_order, split_window,
    )
except ImportError:  # running straight from a source checkout
    from uhi_common import (
        BLOCK_ROWS, S3_BUCKET, SOURCE_LABELS, SW_A, SW_B, SW_C, Cancelled,
        _date, _flatten, bbox_from_point, brightness_temperature, classify,
        find_scene, list_available_dates, source_order, split_window,
    )


def _local_path(local_dir, href):
    wanted = os.path.basename(href).upper()
    for root, _, files in os.walk(local_dir):
        for name in files:
            if name.upper() == wanted:
                return os.path.join(root, name)
    raise RuntimeError("The selected folder does not contain {}".format(os.path.basename(href)))


def _s3_path(href):
    if href.startswith("s3://"):
        return href
    if "/data/" in href:
        return "s3://{}/{}".format(S3_BUCKET, href.split("/data/", 1)[1])
    return href


def source_paths(source, item, local_dir=None, cookie=""):
    """Resolve one source into rasterio paths for B10, B11, QA and MTL.json."""
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
            paths[name] = _s3_path(href)
        elif source == "usgs_https":
            # Without a cookie LandsatLook redirects downloads to the EROS login
            # page; GDAL then fails on the HTML with a cryptic parse error.
            if not cookie:
                raise RuntimeError(
                    "USGS now requires a login cookie for downloads. Add it in "
                    "the form, use an AWS account, or pick a local folder.")
            paths[name] = href
        else:
            raise RuntimeError("Unknown source {}".format(source))
    return paths


def _read_text(path, cookie=""):
    if path.startswith("s3://"):
        bucket, key = path[5:].split("/", 1)
        url = "https://{}.s3.amazonaws.com/{}".format(bucket, key)
        headers = {"User-Agent": "qgis-uhi-plugin/1.0", "x-amz-request-payer": "requester"}
    elif path.startswith("http://") or path.startswith("https://"):
        url, headers = path, {"User-Agent": "qgis-uhi-plugin/1.0"}
        if cookie:
            headers["Cookie"] = cookie
    else:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    with urlopen(Request(url, headers=headers), timeout=45) as response:
        text = response.read().decode("utf-8", "replace")
    # LandsatLook redirects unauthenticated downloads to the EROS login page,
    # which would otherwise surface as a confusing JSON parse error.
    if "<!DOCTYPE html" in text[:200] or "EROS Registration System" in text:
        raise RuntimeError("USGS requires a login for downloads. Add your USGS cookie "
                           "in the form, use an AWS account, or pick a local folder.")
    return text


def load_calibration(mtl_path, cookie=""):
    fields = _flatten(json.loads(_read_text(mtl_path, cookie)), {})
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


def resolve_source(preferred, item, local_dir, cookie, stack):
    attempts = []
    for source in source_order(preferred, local_dir):
        try:
            if source == "aws_s3" and not (os.environ.get("AWS_ACCESS_KEY_ID")
                                           or os.path.isfile(os.path.expanduser("~/.aws/credentials"))):
                raise RuntimeError("No AWS account details found. Add them or choose another option.")
            paths = source_paths(source, item, local_dir, cookie)
            datasets = [stack.enter_context(rasterio.open(paths[key]))
                        for key in ("b10", "b11", "qa")]
            _read_text(paths["mtl"], cookie)  # fail here rather than halfway through
            return source, paths, datasets, attempts
        except Exception as exc:
            attempts.append("{}: {}".format(SOURCE_LABELS.get(source, source), exc))
    raise RuntimeError("Could not get the satellite images. " + " | ".join(attempts))


def _window(dataset, bbox):
    minx, miny, maxx, maxy = transform_bounds("EPSG:4326", dataset.crs, *bbox)
    gt = dataset.transform
    off_x = max(0, int(math.floor((minx - gt.c) / gt.a)))
    off_y = max(0, int(math.floor((gt.f - maxy) / abs(gt.e))))
    width = int(math.ceil((maxx - minx) / abs(gt.a)))
    height = int(math.ceil((maxy - miny) / abs(gt.e)))
    width = min(width, dataset.width - off_x)
    height = min(height, dataset.height - off_y)
    if width <= 0 or height <= 0:
        raise RuntimeError("Your area is outside the available satellite image. "
                           "Try a smaller radius or another date.")
    window = Window(off_x, off_y, width, height)
    return window, dataset.window_transform(window), dataset.crs.to_wkt()


def list_dates(params):
    """Nearby dates with imagery, for the GUI's date picker and failure hint."""
    return list_available_dates(params["lat"], params["lon"], params["radius_km"],
                                _date(params["day"]))


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
    cookie = params.get("cookie") or os.environ.get("USGS_COOKIE", "")

    step(8, "Opening bands")
    with ExitStack() as stack:
        stack.enter_context(rasterio.Env(
            GDAL_HTTP_TIMEOUT="30", GDAL_HTTP_MAX_RETRY="1",
            AWS_REQUEST_PAYER="requester",
            GDAL_HTTP_HEADERS="Cookie: " + cookie if cookie else "",
        ))
        source, paths, datasets, attempts = resolve_source(
            params.get("source", "auto"), item, params.get("local_dir") or None,
            cookie, stack)
        b10, b11, qa = datasets
        cal = load_calibration(paths["mtl"], cookie)

        window, geotransform, projection = _window(b10, bbox)
        width, height = int(window.width), int(window.height)
        step(12, "Computing temperature ({} x {} pixels)".format(width, height))

        label = "".join(c if c.isalnum() or c in "-_" else "_"
                        for c in params.get("location") or "area")
        stamp = _date(params["day"]).isoformat()
        output_dir = params["output_dir"]
        os.makedirs(output_dir, exist_ok=True)
        lst_path = os.path.join(output_dir, "LST_{}_{}.tif".format(label, stamp))
        uhi_path = os.path.join(output_dir, "UHI_{}_{}.tif".format(label, stamp))

        lst_ds = stack.enter_context(rasterio.open(
            lst_path, "w", driver="GTiff", height=height, width=width, count=1,
            dtype="float32", crs=projection, transform=geotransform,
            nodata=-9999.0, compress="lzw", tiled=True))
        count = 0
        total = 0.0
        total_sq = 0.0
        minimum, maximum = math.inf, -math.inf

        for y in range(0, height, BLOCK_ROWS):
            rows = min(BLOCK_ROWS, height - y)
            if cancel and cancel():
                raise Cancelled()
            source_window = Window(window.col_off, window.row_off + y, width, rows)
            dn10 = b10.read(1, window=source_window)
            dn11 = b11.read(1, window=source_window)
            qa_values = qa.read(1, window=source_window)
            tb10 = brightness_temperature(dn10, cal[10])
            tb11 = brightness_temperature(dn11, cal[11])
            celsius = split_window(tb10, tb11) - 273.15
            clear = ((qa_values >> 6) & 1).astype(bool)
            water = ((qa_values >> 7) & 1).astype(bool)
            valid = clear & ~water & np.isfinite(celsius) & (celsius >= -30) & (celsius <= 80)
            values = celsius[valid]
            lst_ds.write(np.where(valid, celsius, -9999.0).astype(np.float32), 1,
                         window=Window(0, y, width, rows))
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

        step(88, "Writing UHI classes")
        uhi_ds = stack.enter_context(rasterio.open(
            uhi_path, "w", driver="GTiff", height=height, width=width, count=1,
            dtype="uint8", crs=projection, transform=geotransform,
            nodata=0, compress="lzw", tiled=True))
        with stack.enter_context(rasterio.open(lst_path)) as lst_reader:
            for y in range(0, height, BLOCK_ROWS):
                rows = min(BLOCK_ROWS, height - y)
                if cancel and cancel():
                    raise Cancelled()
                values = lst_reader.read(1, window=Window(0, y, width, rows))
                values = np.where(values <= -9990, np.nan, values)
                uhi_ds.write(classify(values, edges), 1, window=Window(0, y, width, rows))

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
        for dataset in (lst_ds, uhi_ds):
            dataset.update_tags(**{key: str(value) for key, value in provenance.items()})

    step(100, "Done")
    return {
        "lst_path": lst_path, "uhi_path": uhi_path,
        "stats": {"mean": mean, "min": minimum, "max": maximum,
                  "valid_pixels": count, "edges": edges},
        "source": source, "attempts": attempts, "item": item,
    }
