# Urban Heat Island desktop app

A standalone app that does the same job as the QGIS plugin, without running QGIS.
It shares the discovery and temperature maths (`uhi_landsat/uhi_common.py`) with the
plugin, but uses rasterio for raster I/O so a plain `pip install` is all an end user
needs.

![Icon](icon.png)

## What it does

Enter a date, a place name, the latitude and longitude of the centre of your area,
and a radius. The app finds the clearest nearby Landsat satellite image and saves
two GeoTIFF maps in your chosen folder:

- a ground temperature map in °C;
- a five-zone urban heat island map (cool, slightly cool, average, warm, hot spot).

Clouds and water are left out automatically. A progress bar keeps you informed and
**Cancel** stops the work.

Not sure which date has an image? Click **Dates with images** to list the nearest
dates within ±60 days with their cloud cover, then double-click one to use it. If a
search finds nothing, the error message includes that date list automatically.

## Requirements

Just Python 3.10 or newer. `pip` installs everything else:

- `numpy` — processing;
- `PySide6-Essentials` — the Qt GUI;
- `rasterio` — GeoTIFF and remote raster I/O (its wheels bundle GDAL).

No Tk, no system GDAL, no conda, no OS package manager.

## Install and run

```bash
pip install uhi-desktop
uhi-desktop
```

The first install downloads the Qt and GDAL wheels (roughly 120 MB); after that it
just works on Linux, macOS and Windows. You can also run it as
`python -m desktop_app.uhi_desktop`.

### From a source checkout

```bash
pip install .
python desktop_app/uhi_desktop.py
```

Keep `desktop_app/` and `uhi_landsat/` together when running from source.

## Getting the satellite data

The app offers the same three choices as the plugin:

| Option | What you need |
|---|---|
| **Folder on my computer** | Satellite files already downloaded from USGS EarthExplorer into one folder |
| **AWS account** | An Amazon Web Services account (a few cents per download) |
| **USGS website login** | Your USGS EarthExplorer login cookie |

Leave it on **Automatic - try all options** and it will use the first option that
works. Full step-by-step guidance is in the [main README](../README.md).

## Notes

- The app never freezes: downloads and processing run in a background thread.
- Memory use stays near 100 MB even for large areas, because the images are
  processed in 512-row blocks.
- Every output GeoTIFF records provenance: source used, scene ID, acquisition time,
  band paths, retrieval time, licence and coefficients.
- Results are relative to the area and date you chose, so compare different dates
  only when you use the same area and settings.

## Tests

```bash
QT_QPA_PLATFORM=offscreen python3 tests/test_desktop.py
```

This builds a small synthetic Landsat scene with rasterio and runs the whole worker
path without network access.
