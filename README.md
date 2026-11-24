# Urban Heat Island QGIS plugin (Landsat 10/11)

![Urban Heat Island plugin icon](uhi_landsat/icon.svg)

A QGIS tool for GIS analysts: give it a place, a date and a radius, and it makes
two ready-to-use maps showing how hot the ground is and where the hottest parts
of the city are.

## What it does

The plugin looks for a Landsat satellite image of your area close to the date you
choose, reads the two heat-sensing channels on the satellite, and adds two layers
to your QGIS project:

- **LST (land surface temperature)** — a colour map of how warm the ground is, in °C.
  Blue is cooler, red is hotter.
- **UHI (urban heat island)** — an easier-to-read map with five heat zones:
  cool, slightly cool, average, warm, and hot spot.

You do not need to know how to process satellite imagery. You just fill in a
short form and the maps appear in your layer panel.

**No QGIS?** There is also a standalone desktop app with exactly the same logic in
[`desktop_app/`](desktop_app/README.md).

## Install

Download `uhi_landsat_plugin_v1.0.0.zip` from the
[latest release](https://github.com/krishnaduttPanchagnula/qgis-urban-heat-island/releases/latest),
or install straight from QGIS:

1. *Plugins → Manage and Install Plugins → Settings…*
2. Tick **Add new repository**, name it `Urban Heat Island`, and use this URL:
   `https://raw.githubusercontent.com/krishnaduttPanchagnula/qgis-urban-heat-island/main/plugins.xml`
3. Search for **Urban Heat Island (Landsat 10/11)** and click *Install Plugin*.

Manual install: unzip the release into your QGIS Python plugins folder, e.g.
`~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`, so that
`uhi_landsat/metadata.txt` sits directly under that folder. Restart QGIS and
enable the plugin in *Plugins → Manage and Install Plugins*. The city/heat icon
appears in the plugin manager and on the toolbar.

No extra software is needed — QGIS already includes everything the plugin needs.

## Before you start: getting the satellite data

The satellite files are free, but you need one of these three ways to reach them.
Pick whichever is easiest for you:

| Option | What you need | Best for |
|---|---|---|
| **Folder on my computer** | The satellite files already downloaded from USGS EarthExplorer into one folder | Offline work, or if you do not have cloud accounts |
| **AWS account** | An Amazon Web Services account (downloads cost a few cents) | Automatic download, no manual searching |
| **USGS website login** | Your USGS EarthExplorer login cookie | Automatic download with an existing USGS account |

Leave the source on **Automatic – try all options** and the plugin will try each
available option in turn and use the first one that works. If one fails, you will
see the reason and it will move to the next one.

## How to use it

1. Click the heat-island icon in the QGIS toolbar (or find it in the *Plugins* menu).
2. **Date** — choose the day you are interested in. A dry, cloud-free day works best.
   If there is no satellite pass on that exact day, the plugin looks up to one week
   before or after and uses the clearest image it finds.
3. **Location name** — type a name such as `Milan`. This is only used to name the
   output layers.
4. **Latitude / Longitude** — the centre of the area. You can read these off the
   QGIS status bar by clicking on the map.
5. **Radius (km)** — how far around that point to map. 20 km is a good start.
6. **Output folder** — where the two maps will be saved.
7. **Where to get the data** — choose one of the three options above. If you pick
   *Folder on my computer*, click `...` and select the folder.
8. Click **OK**. A progress bar appears; you can cancel at any time. QGIS stays
   usable while it works.

Two layers then appear in your layer panel, already coloured and ready to use.
A message at the bottom of QGIS tells you which data source worked and the average
temperature for your area.

## Understanding the maps

- The **LST** layer shows actual ground temperature in °C. Use it to compare
  neighbourhoods or to find the warmest surfaces.
- The **UHI** layer sorts the area into five zones relative to the average for
  that image: `1` cool, `2` slightly cool, `3` average, `4` warm, `5` hot spot.
  Value `0` means no result — usually cloud or water.
- The zones are **relative to the area and date you chose**. They are excellent
  for spotting heat patterns within one map, but two maps from different dates
  should only be compared if you used the same area and settings.

## Practical tips

- Cloudy days hide the ground. Try a dry month, or let the plugin pick the
  clearest nearby date.
- Cloudy and water pixels are automatically left out, so they will not distort
  your results.
- Larger radii take longer to process. Start with 20 km and increase if needed.
- Only Landsat 8 and 9 are supported (the satellites with heat channels 10 and 11).
- Something not working? Open an
  [issue](https://github.com/krishnaduttPanchagnula/qgis-urban-heat-island/issues)
  with the message from QGIS and the date and place you entered.

## For the technically minded

Scene discovery uses the public USGS LandsatLook and Element84 Earth Search STAC
catalogues. Band files come from the first working of: local folder, the
requester-pays `usgs-landsat` S3 bucket, or `landsatlook.usgs.gov` with a session
cookie. Temperature is a split-window estimate,
`T = T10 + A·(T10−T11) + B·(T10−T11)² + C` with A=1, B=0, C=0, after radiance and
brightness-temperature conversion using the scene MTL file. Clear-sky pixels are
selected with QA_PIXEL bits 6 and 7. Processing is streamed in 512-row blocks, so
peak memory stays near 100 MB. Every output GeoTIFF stores provenance: source used,
scene ID, acquisition time, band paths, retrieval time, licence and coefficients.

`shortcut: the split-window uses fixed coefficients (clear sky, ~0.97 emissivity),
so this is a relative heat-island product, not a calibrated absolute temperature.
For absolute LST, use USGS Collection 2 Level-2 ST_B10.`

## Tests

- `python3 tests/test_core.py` — pure logic, no QGIS needed.
- `python3 tests/test_integration.py` — end-to-end with real GDAL bindings.
- `python3 tests/test_desktop.py` — desktop app worker with real GDAL bindings.
