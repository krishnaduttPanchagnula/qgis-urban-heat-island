# Urban Heat Island desktop app

A small standalone app that does exactly the same job as the QGIS plugin, without
running QGIS. It uses the **same shared engine** (`uhi_landsat/uhi_core.py`), so the
maps it produces are identical.

![Icon](icon.png)

## What it does

Enter a date, a place name, the latitude and longitude of the centre of your area,
and a radius. The app finds the clearest nearby Landsat satellite image and saves
two GeoTIFF maps in your chosen folder:

- a ground temperature map in °C;
- a five-zone urban heat island map (cool, slightly cool, average, warm, hot spot).

Clouds and water are left out automatically. A progress bar keeps you informed and
**Cancel** stops the work.

## Requirements

- Python 3.9 or newer
- `numpy`
- GDAL Python bindings (`from osgeo import gdal`)

The easy way to get GDAL is a conda environment:

```bash
conda create -n uhi python=3.11 numpy gdal -c conda-forge
conda activate uhi
python desktop_app/uhi_desktop.py
```

If you already have QGIS installed, its Python interpreter has everything needed.

## Run it

From the repository root:

```bash
python desktop_app/uhi_desktop.py
```

Double-clicking `uhi_desktop.py` also works on most systems if `.py` files are
associated with Python.

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

- Keep the two folders together: `desktop_app/` imports the engine from
  `../uhi_landsat/`.
- The app never freezes: downloads and processing run in a background thread.
- Memory use stays near 100 MB even for large areas, because the images are
  processed in small blocks.
- Results are relative to the area and date you chose, so compare different dates
  only when you use the same area and settings.
