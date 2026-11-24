# Changelog

All notable changes to this plugin are documented here.

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
