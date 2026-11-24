# Urban Heat Island QGIS plugin (Landsat 10/11)

A QGIS 3 plugin: give it a date, location name, latitude/longitude and a radius (km).
It finds the least-cloudy Landsat 8/9 scene within ±7 days, reads thermal bands
10 and 11, computes land surface temperature (LST), and adds two styled layers:

- `LST_<location>_<date>` — continuous surface temperature in °C (Float32)
- `UHI_<location>_<date>` — five heat-intensity classes (Byte, 0 = no data)

## Download and install

Download `uhi_landsat_plugin.zip` from the
[latest release](https://github.com/krishnaduttPanchagnula/qgis-urban-heat-island/releases/latest),
or install straight from QGIS:

1. *Plugins → Manage and Install Plugins → Settings…*
2. Tick **Add new repository**, name it `Urban Heat Island`, and use this URL:
   `https://raw.githubusercontent.com/krishnaduttPanchagnula/qgis-urban-heat-island/main/plugins.xml`
3. Search for **Urban Heat Island (Landsat 10/11)** and click *Install Plugin*.

Manual install: unzip the release into your QGIS Python plugins folder, e.g.
`~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`, so that
`uhi_landsat/metadata.txt` sits directly under that folder. Restart QGIS and
enable the plugin in *Plugins → Manage and Install Plugins*. The city/heat SVG
icon appears in the plugin manager and on the plugin toolbar.

No pip dependencies: QGIS already ships numpy and GDAL.

## Multiple data sources with fallback

Scene discovery tries two public catalogues in order:

1. **USGS LandsatLook STAC** — has exact Level-1 B10/B11 asset names (primary).
2. **Element84 Earth Search STAC** — used if the first catalogue is unreachable;
   its Level-2 scene ID is converted to the matching Level-1 product ID.

Band download tries sources in this order (the **Data source** combo lets you pin one):

| Source | Needs | Notes |
|---|---|---|
| **Local folder** | B10/B11/QA_PIXEL/MTL.json downloaded by you | First choice when a folder is entered; fully offline |
| **AWS S3** | `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` or `~/.aws/credentials` | `usgs-landsat` bucket is requester-pays; small egress charge |
| **USGS HTTPS** | a logged-in USGS session cookie | Paste the cookie into the field, or set `USGS_COOKIE` in the environment |

With **Auto (try all sources)**, each source is attempted and the first one whose
bands and MTL open successfully wins. If all fail, the message bar shows the reason
from every source. The source actually used is shown in the success message and
stored as a `SOURCE_USED` tag on the output rasters.

Be aware: anonymous access to the S3 bucket (403) and to USGS HTTPS (login redirect)
does not work — that is why credentials or a local folder are required for recent data.

## Responsiveness and memory

- Everything runs in a `QgsTask` (worker thread): catalogue search, downloads and
  raster computation never block the QGIS GUI, and **Cancel** is honoured between blocks.
- No `gdal.Warp(... format="MEM")` and no whole-scene arrays. A common pixel window
  is computed from B10's geotransform; B10, B11 and QA_PIXEL are read in 512-row
  blocks, LST is streamed to a GeoTIFF, then the UHI classes are streamed from that
  local file. Peak RAM is a few MB per block regardless of radius; UHI is stored
  as one byte per pixel.
- Outputs use LZW + tiling with 256px blocks, so QGIS pans/zooms without loading
  the whole raster.

## Algorithm

1. B10/B11 DN → TOA radiance using `RADIANCE_MULT/ADD_BAND_10/11` from `MTL.json`.
2. Radiance → brightness temperature using the scene's `K1`/`K2` thermal constants.
3. Split-window LST: `T = TB10 + A·(TB10−TB11) + B·(TB10−TB11)² + C`, then −273.15 °C.
   Current coefficients: A=1, B=0, C=0 (module constants in `uhi_core.py`).
4. Clear-sky mask from `QA_PIXEL` (bit 6 clear, bit 7 water excluded) plus a
   −30…80 °C sanity range.
5. UHI classes are based on the mean and standard deviation accumulated over all
   blocks: `< mean−0.5σ`, `mean−0.5σ…mean`, `mean…mean+0.5σ`,
   `mean+0.5σ…mean+1.5σ`, `≥ mean+1.5σ`.

`shortcut: the split-window uses fixed coefficients (clear sky, ~0.97 emissivity),
so this is a relative heat-island product, not a calibrated absolute temperature.
For quantitative absolute LST, use USGS Collection 2 Level-2 ST_B10, which has
per-pixel atmospheric and emissivity correction.`

Every output GeoTIFF carries provenance tags: source used, sources tried, scene ID,
acquisition time, band paths, retrieval time, license and algorithm coefficients.

## Limits

- UHI classes are relative to the clipped scene; different dates are directly
  comparable only if you use the same extent and calibration.
- Landsat 8/9 only (thermal bands 10/11); Landsat 5/7 band 6 is not supported.
- Requested areas outside the selected scene are rejected rather than padded.
- Google's public Landsat bucket was checked but only holds ~2015 data, so it is
  not offered as a source.

## Repository branding

The repo About has a description, homepage pointing to the latest release, and topics
(`qgis`, `qgis-plugin`, `landsat`, `urban-heat-island`, `land-surface-temperature`,
`remote-sensing`, `thermal-imaging`, `geospatial`, `gis`, `python`, `raster`,
`heat-island`, `stac`, `open-data`).

The plugin/app icon is `uhi_landsat/icon.svg` — it appears in QGIS's plugin manager
and toolbar and in the custom-repository listing. `social-preview.svg` and its
rendered `social-preview.png` provide the repo social-preview image. GitHub does not
expose an API for that image, so upload `social-preview.png` once under
*Settings → General → Social preview*.

## Test

`python3 tests/test_core.py` — checks the pure logic without QGIS installed.
`python3 tests/test_integration.py` — end-to-end test with real GDAL bindings.
