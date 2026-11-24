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
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
from osgeo import gdal, osr

STAC_CATALOGS = [
    ("USGS LandsatLook", "https://landsatlook.usgs.gov/stac-server/search"),
    ("Element84 Earth Search", "https://earth-search.aws.element84.com/v1/search"),
]
S3_BUCKET = "usgs-landsat"
BLOCK_ROWS = 512

# shortcut: fixed split-window coefficients assume clear sky, mid-latitude
# atmosphere and emissivity ~0.97; replace with USGS C2 L2 ST (per-pixel
# atmospheric/emissivity correction) if absolute temperatures are needed.
SW_A = 1.0
SW_B = 0.0
SW_C = 0.0

CLASS_LABELS = {
    1: "Cool (below rural mean)",
    2: "Slightly cool",
    3: "Near mean",
    4: "Warm",
    5: "Urban hot spot",
}

SOURCE_LABELS = {
    "auto": "Auto (try all sources)",
    "local": "Local folder",
    "aws_s3": "AWS S3 (requester-pays)",
    "usgs_https": "USGS HTTPS (login cookie)",
}


class Cancelled(Exception):
    """Raised when the user cancels the QgsTask."""
    pass


def bbox_from_point(lat, lon, radius_km):
    """WGS84 bounding box for a radius around a point, in degrees."""
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Latitude must be in [-90, 90] and longitude in [-180, 180].")
    if radius_km <= 0:
        raise ValueError("Radius must be greater than zero.")
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * max(0.01, math.cos(math.radians(lat))))
    return [lon - dlon, lat - dlat, lon + dlon, lat + dlat]


def _get_json(url):
    request = Request(url, headers={"User-Agent": "qgis-uhi-plugin/1.0"})
    with urlopen(request, timeout=45) as response:
        return json.load(response)


def _date(iso):
    if isinstance(iso, date):
        return iso
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).date()


def _query_catalog(url, collection, bbox, day, days, cloud_field):
    params = {
        "collections": collection,
        "bbox": ",".join(f"{v:.6f}" for v in bbox),
        "datetime": f"{day - timedelta(days=days)}T00:00:00Z/"
                    f"{day + timedelta(days=days)}T23:59:59Z",
        "limit": "50",
    }
    features = _get_json(url + "?" + urlencode(params)).get("features", [])
    candidates = []
    for feature in features:
        props = feature.get("properties", {})
        if props.get("platform") not in ("landsat-8", "landsat-9"):
            continue
        cloud = props.get(cloud_field, props.get("landsat:cloud_cover_land"))
        if cloud is None or cloud > 50:
            continue
        acquired = _date(props["datetime"])
        candidates.append((abs((acquired - day).days), cloud, feature))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates


def _synthesise_l1_item(feature):
    """Turn an Earth Search L2 item into LandsatLook-shaped L1 asset hrefs."""
    parts = feature["id"].split("_")
    if len(parts) < 7 or not parts[2].isdigit():
        return None
    sensor, pathrow, acquired = parts[0], parts[2], parts[3]
    category = parts[5]
    path, row = pathrow[:3], pathrow[3:]
    l1 = "{}_L1GT_{}_{}_{}_{}_{}".format(sensor, pathrow, acquired, acquired, category, parts[6])
    base = "https://landsatlook.usgs.gov/data/collection02/level-1/standard/oli-tirs/{}/{}/{}/{}".format(
        acquired[:4], path, row, l1)
    assets = {
        "lwir11": {"href": base + "/" + l1 + "_B10.TIF"},
        "lwir12": {"href": base + "/" + l1 + "_B11.TIF"},
        "qa_pixel": {"href": base + "/" + l1 + "_QA_PIXEL.TIF"},
        "MTL.json": {"href": base + "/" + l1 + "_MTL.json"},
    }
    return {"id": l1, "properties": feature["properties"], "assets": assets,
            "links": feature.get("links", [])}


def find_scene(lat, lon, radius_km, day, days=7):
    """Pick the best Landsat 8/9 scene; fall back to the second catalogue."""
    bbox = bbox_from_point(lat, lon, radius_km)
    failures = []
    for name, url in STAC_CATALOGS:
        try:
            collection = "landsat-c2l1" if "LandsatLook" in name else "landsat-c2-l2"
            cloud_field = "landsat:cloud_cover_land" if "LandsatLook" in name else "eo:cloud_cover"
            candidates = _query_catalog(url, collection, bbox, day, days, cloud_field)
            if not candidates:
                failures.append("{}: no scene found".format(name))
                continue
            _, _, feature = candidates[0]
            if "LandsatLook" not in name:
                feature = _synthesise_l1_item(feature)
                if feature is None:
                    failures.append("{}: unrecognised scene id".format(name))
                    continue
            return bbox, feature
        except Exception as exc:
            failures.append("{}: {}".format(name, exc))
    raise RuntimeError("Scene search failed. " + "; ".join(failures))


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
    raise RuntimeError("No file named {}".format(os.path.basename(href)))


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


def source_order(preferred, local_dir):
    if preferred != "auto":
        return [preferred]
    order = ["aws_s3", "usgs_https"]
    if local_dir:
        order.insert(0, "local")
    return order


def _prepare_source(source, cookie):
    gdal.SetConfigOption("GDAL_HTTP_TIMEOUT", "30")
    gdal.SetConfigOption("GDAL_HTTP_MAX_RETRY", "1")
    if source == "aws_s3":
        gdal.SetConfigOption("AWS_REQUEST_PAYER", "requester")
        if not (os.environ.get("AWS_ACCESS_KEY_ID")
                or os.path.isfile(os.path.expanduser("~/.aws/credentials"))):
            raise RuntimeError("No AWS credentials found")
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
        raise RuntimeError("raster not accessible")
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
    raise RuntimeError("No data source worked. " + " | ".join(attempts))


def _read_text(path):
    if path.startswith("/vsi"):
        handle = gdal.VSIFOpenL(path, "rb")
        if handle is None:
            raise RuntimeError("cannot open " + path)
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


def _flatten(obj, out):
    if isinstance(obj, dict):
        for value in obj.values():
            _flatten(value, out)
    elif isinstance(obj, list):
        for value in obj:
            _flatten(value, out)
    return out


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


def brightness_temperature(dn, cal):
    radiance = cal["mult"] * np.asarray(dn, dtype=np.float32) + cal["add"]
    with np.errstate(divide="ignore", invalid="ignore"):
        temp = cal["k2"] / np.log(cal["k1"] / radiance + 1.0)
    temp[~np.isfinite(temp)] = np.nan
    return temp


def split_window(tb10, tb11):
    difference = tb10 - tb11
    return tb10 + SW_A * difference + SW_B * difference ** 2 + SW_C


def class_edges(celsius):
    mean = float(np.nanmean(celsius))
    spread = float(np.nanstd(celsius))
    if not np.isfinite(spread) or spread < 0.1:
        spread = 1.0
    return [mean - 0.5 * spread, mean, mean + 0.5 * spread, mean + 1.5 * spread]


def classify(celsius, edges):
    classes = np.ones(celsius.shape, dtype=np.uint8)
    for value, edge in enumerate(edges, start=2):
        classes = np.where(celsius >= edge, value, classes)
    classes[~np.isfinite(celsius)] = 0
    return classes


def _window(dataset, bbox):
    source = osr.SpatialReference()
    source.ImportFromWkt(dataset.GetProjectionRef())
    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    transform = osr.CoordinateTransformation(wgs84, source)
    x1, y1, _ = transform.TransformPoint(bbox[0], bbox[1])
    x2, y2, _ = transform.TransformPoint(bbox[2], bbox[3])
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
        raise RuntimeError("Requested area is outside the Landsat scene")
    out_gt = (gt[0] + off_x * gt[1], gt[1], gt[2],
              gt[3] + off_y * gt[5], gt[4], gt[5])
    return off_x, off_y, width, height, out_gt, dataset.GetProjectionRef()


def _create(path, width, height, geotransform, projection, dtype, nodata):
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(path, width, height, 1, dtype,
                            ["COMPRESS=LZW", "TILED=YES", "BLOCKSIZE=256"])
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
            raise RuntimeError("No valid clear-sky pixels in the requested area")
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
