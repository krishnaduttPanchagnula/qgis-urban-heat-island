"""Pure discovery and temperature logic shared by the QGIS and pip backends.

No GDAL, QGIS or Qt imports here, so both backends can use it on a bare Python
install.
"""

import json
import math
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np

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
    "auto": "Automatic",
    "local": "Folder on my computer",
    "aws_s3": "AWS account",
    "usgs_https": "USGS website login",
}


class Cancelled(Exception):
    """Raised when the user cancels the task."""
    pass


def source_order(preferred, local_dir):
    if preferred != "auto":
        return [preferred]
    order = ["aws_s3", "usgs_https"]
    if local_dir:
        order.insert(0, "local")
    return order


def bbox_from_point(lat, lon, radius_km):
    """WGS84 bounding box for a radius around a point, in degrees."""
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Latitude must be in [-90, 90] and longitude in [-180, 180].")
    if radius_km <= 0:
        raise ValueError("Radius must be greater than zero.")
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * max(0.01, math.cos(math.radians(lat))))
    return [lon - dlon, lat - dlat, lon + dlon, lat + dlat]


def _get_json(url, headers=None):
    request = Request(url, headers=headers or {"User-Agent": "qgis-uhi-plugin/1.0"})
    with urlopen(request, timeout=45) as response:
        return json.load(response)


def _date(iso):
    if isinstance(iso, date):
        return iso
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).date()


def _query_catalog(url, collection, bbox, day, days, cloud_field,
                   limit=50, max_cloud=50):
    params = {
        "collections": collection,
        "bbox": ",".join(f"{v:.6f}" for v in bbox),
        "datetime": f"{day - timedelta(days=days)}T00:00:00Z/"
                    f"{day + timedelta(days=days)}T23:59:59Z",
        "limit": str(limit),
    }
    features = _get_json(url + "?" + urlencode(params)).get("features", [])
    candidates = []
    for feature in features:
        props = feature.get("properties", {})
        # LandsatLook reports "LANDSAT_9", Element84 reports "landsat-9".
        platform = str(props.get("platform", "")).lower().replace("_", "-").replace(" ", "-")
        if platform not in ("landsat-8", "landsat-9"):
            continue
        cloud = props.get(cloud_field, props.get("landsat:cloud_cover_land"))
        if cloud is None or (max_cloud is not None and cloud > max_cloud):
            continue
        acquired = _date(props["datetime"])
        candidates.append((abs((acquired - day).days), cloud, feature))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates


def _synthesise_l1_item(feature):
    """Turn an Earth Search L2 item into LandsatLook-shaped L1 asset hrefs."""
    parts = feature["id"].split("_")
    # Old ids carry a processing date (LC09_L2SP_ppprrr_YYYYMMDD_YYYYMMDD_02_T1);
    # newer ones do not (LC09_L2SP_ppprrr_YYYYMMDD_02_T1).
    if len(parts) < 6 or not parts[2].isdigit() or not parts[3].isdigit():
        return None
    sensor, pathrow, acquired = parts[0], parts[2], parts[3]
    category, tier = parts[-2], parts[-1]
    path, row = pathrow[:3], pathrow[3:]
    l1 = "{}_L1GT_{}_{}_{}_{}_{}".format(sensor, pathrow, acquired, acquired, category, tier)
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
    raise RuntimeError("Could not find a suitable satellite image near the chosen date. "
                       + "; ".join(failures))


def list_available_dates(lat, lon, radius_km, day, days=60):
    """Dates with Landsat imagery near `day`: [(iso_date, cloud, scene_id, catalogue)].

    One row per date, keeping the clearest scene. Cloudy dates are included so
    users can see that data exists even if no scene passes the 50% cloud cut.
    """
    bbox = bbox_from_point(lat, lon, radius_km)
    best = {}
    for name, url in STAC_CATALOGS:
        try:
            collection = "landsat-c2l1" if "LandsatLook" in name else "landsat-c2-l2"
            cloud_field = "landsat:cloud_cover_land" if "LandsatLook" in name else "eo:cloud_cover"
            candidates = _query_catalog(url, collection, bbox, day, days, cloud_field,
                                        limit=100, max_cloud=None)
        except Exception:
            continue
        for _, cloud, feature in candidates:
            if cloud is None or cloud < 0:  # unknown cloud cover
                continue
            acquired = _date(feature["properties"]["datetime"]).isoformat()
            if acquired not in best or cloud < best[acquired][0]:
                best[acquired] = (cloud, feature["id"], name)
    return [(date, best[date][0], best[date][1], best[date][2]) for date in sorted(best)]


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


def _flatten(obj, out):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, (dict, list)):
                _flatten(value, out)
            else:
                out[key] = value
    elif isinstance(obj, list):
        for value in obj:
            _flatten(value, out)
    return out
