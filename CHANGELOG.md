# Changelog

All notable changes to this project are documented here.

## [1.2.2] - 2026-11-25

### Added
- "Dates with images" button and failure hints: list the nearest dates with
  Landsat imagery within ±60 days, with cloud cover, and pick one to fill the form.

## [1.2.1] - 2026-11-24

### Fixed
- Scene search now accepts LandsatLook's uppercase platform values (`LANDSAT_9`)
  as well as Element84's lowercase ones, so scenes are found again.
- Element84 L2 scene ids without a processing date are now converted to L1 ids
  correctly, so the fallback catalogue works with current data.

## [1.2.0] - 2026-11-24

### Changed
- Desktop app now uses PySide6 (Qt) instead of tkinter and rasterio instead of
  system GDAL bindings. `pip install uhi-desktop` needs no system libraries.
- Shared maths/discovery moved to `uhi_landsat/uhi_common.py`; the QGIS plugin
  keeps its osgeo/GDAL backend, so QGIS users need no extra packages.

## [1.1.0] - 2026-11-24

### Added
- Standalone desktop app (`desktop_app/`) with the same workflow and the same
  shared engine as the QGIS plugin, using a background thread so it never freezes.
- Downloadable desktop zip containing the app and the shared engine, installable
  with `pip` (`uhi-desktop` command), plus conda and source-checkout instructions.
- Plain-language descriptions in the README, plugin metadata, repository listing
  and the dialogs, so GIS analysts can use it without processing satellite data manually.

## [1.0.0] - 2026-11-24

### Added
- Date / location / lat-lon / radius dialog for Landsat 8/9 urban heat island mapping.
- Two STAC catalogues for scene discovery: USGS LandsatLook and Element84 Earth Search.
- Fallback chain for band access: local folder -> AWS S3 (requester-pays) -> USGS HTTPS (session cookie), with a source selector and per-source failure reporting.
- Split-window land surface temperature from bands 10 and 11, clear-sky/water masking from QA_PIXEL.
- Styled `LST` (°C) and `UHI` class raster layers, added to the QGIS canvas.
- Provenance metadata on every output GeoTIFF (source used, scene ID, acquisition, band paths, retrieval time, licence, algorithm).
- `QgsTask` background execution, progress reporting and cancellation.
- SVG toolbar / plugin-manager icon.

### Notes
- The split-window uses fixed coefficients, so the product is a relative heat-island
  map rather than a calibrated absolute temperature. See the README.
