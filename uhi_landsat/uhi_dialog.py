import os

from qgis.PyQt import uic
from qgis.PyQt.QtCore import QDate
from qgis.PyQt.QtWidgets import QDialog, QFileDialog

FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), "uhi_dialog.ui"))


class UhiDialog(FORM_CLASS, QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.dateEdit.setDate(QDate.currentDate().addDays(-30))
        self.tbOutputDir.clicked.connect(self._pick_output)
        self.tbLocalDir.clicked.connect(self._pick_local)

    def _pick_output(self):
        path = QFileDialog.getExistingDirectory(self, "Output folder", self.leOutputDir.text())
        if path:
            self.leOutputDir.setText(path)

    def _pick_local(self):
        path = QFileDialog.getExistingDirectory(self, "Folder with B10/B11 files",
                                                self.leLocalDir.text())
        if path:
            self.leLocalDir.setText(path)

    def params(self):
        day = self.dateEdit.date()
        sources = ["auto", "local", "aws_s3", "usgs_https"]
        return {
            "day": "{:04d}-{:02d}-{:02d}".format(day.year(), day.month(), day.day()),
            "location": self.leLocation.text().strip() or "area",
            "lat": self.dsLat.value(),
            "lon": self.dsLon.value(),
            "radius_km": self.dsRadius.value(),
            "output_dir": self.leOutputDir.text().strip() or os.path.expanduser("~/uhi_output"),
            "local_dir": self.leLocalDir.text().strip(),
            "source": sources[self.cmbSource.currentIndex()],
            "cookie": self.leCookie.text().strip(),
        }
