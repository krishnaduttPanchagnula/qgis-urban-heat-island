"""Simple in-app viewer for the two output GeoTIFFs.

No matplotlib or extra dependency: rasterio reads the file, numpy applies a
colour ramp, and Qt presents it with layer switching and zoom.
"""

import numpy as np
import rasterio
from rasterio.enums import Resampling

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QScrollArea,
    QVBoxLayout,
)

try:  # pip-installed package
    from uhi_landsat.uhi_common import CLASS_LABELS
except ImportError:  # source checkout
    from uhi_common import CLASS_LABELS

# Blue -> cyan -> yellow -> orange -> red, indexed by normalised temperature.
_LST_STOPS = [
    (0.00, (20, 40, 150)), (0.25, (0, 170, 230)), (0.50, (240, 235, 90)),
    (0.75, (245, 150, 40)), (1.00, (180, 25, 25)),
]
_UHI_COLORS = {
    0: (0, 0, 0, 0), 1: (40, 80, 180, 255), 2: (90, 180, 220, 255),
    3: (190, 200, 180, 255), 4: (245, 150, 50, 255), 5: (195, 30, 30, 255),
}
_LUT = np.stack([
    np.interp(np.linspace(0, 1, 256), [s[0] for s in _LST_STOPS],
              [s[1][channel] for s in _LST_STOPS])
    for channel in range(3)
], axis=1).astype(np.uint8)


def render(path, kind, vmin=None, vmax=None, max_side=1400):
    """Return a QImage of the raster with nodata transparent."""
    with rasterio.open(path) as dataset:
        scale = min(1.0, max_side / max(dataset.width, dataset.height))
        height = max(1, round(dataset.height * scale))
        width = max(1, round(dataset.width * scale))
        data = dataset.read(1, out_shape=(height, width),
                            resampling=Resampling.nearest)

    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    if kind == "uhi":
        for value, color in _UHI_COLORS.items():
            rgba[data == value] = color
    else:
        valid = np.isfinite(data) & (data > -9990)
        if vmin is None or vmax is None or vmax <= vmin:
            sample = data[valid]
            vmin, vmax = (float(sample.min()), float(sample.max())) if sample.size else (0.0, 1.0)
        index = np.clip(((data - vmin) / (vmax - vmin)) * 255, 0, 255).astype(np.uint8)
        rgba[:, :, :3] = _LUT[index]
        rgba[valid, 3] = 255
    return QImage(rgba.data, width, height, width * 4,
                  QImage.Format.Format_ARGB32).copy()


class MapViewer(QDialog):
    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Urban heat island maps")
        self.resize(900, 650)
        self.result = result

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.layer = QComboBox()
        self.layer.addItems(["LST (°C)", "UHI zones"])
        self.zoom = QComboBox()
        self.zoom.addItems(["Fit", "100%", "200%"])
        controls.addWidget(QLabel("Layer"))
        controls.addWidget(self.layer)
        controls.addWidget(QLabel("Zoom"))
        controls.addWidget(self.zoom)
        layout.addLayout(controls)

        self.scroll = QScrollArea()
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.image)
        layout.addWidget(self.scroll, 1)

        self.legend = QLabel()
        self.legend.setWordWrap(True)
        layout.addWidget(self.legend)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.clicked.connect(self.reject)
        layout.addWidget(buttons)

        self.layer.currentIndexChanged.connect(self._render)
        self.zoom.currentIndexChanged.connect(self._render)
        self._render()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.zoom.currentIndex() == 0:
            self._scale()

    def _render(self):
        stats = self.result["stats"]
        if self.layer.currentIndex() == 0:
            self._pixmap = QPixmap.fromImage(render(
                self.result["lst_path"], "lst", stats["min"], stats["max"]))
            self.legend.setText(
                "Ground temperature {:.1f} to {:.1f} °C. "
                "Blue = cool, red = hot; grey = cloud or water.".format(
                    stats["min"], stats["max"]))
        else:
            self._pixmap = QPixmap.fromImage(render(self.result["uhi_path"], "uhi"))
            rows = []
            for value in sorted(CLASS_LABELS):
                color = _UHI_COLORS[value]
                rows.append('<span style="color:rgb({},{},{})">&#9608;</span> {} {}'
                            .format(*color[:3], value, CLASS_LABELS[value]))
            self.legend.setText(" &nbsp; ".join(rows))
        self._scale()

    def _scale(self):
        choice = self.zoom.currentIndex()
        if choice == 0:
            size = self.scroll.viewport().size()
            pixmap = self._pixmap.scaled(size.width() - 4, size.height() - 4,
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
        else:
            factor = 1 if choice == 1 else 2
            pixmap = self._pixmap.scaled(self._pixmap.width() * factor,
                                         self._pixmap.height() * factor,
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.FastTransformation)
        self.image.setPixmap(pixmap)
        self.image.resize(pixmap.size())
