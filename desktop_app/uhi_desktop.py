"""Standalone desktop app for the urban heat island workflow.

Uses rasterio (whose wheels bundle GDAL) so a plain `pip install uhi-desktop`
needs no system libraries. The maths is shared with the QGIS plugin.
"""

import os
import queue
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

HERE = os.path.dirname(os.path.abspath(__file__))
try:  # pip-installed package
    from desktop_app import uhi_pip_core as core
    from desktop_app.map_viewer import MapViewer
except ImportError:  # running straight from a source checkout
    import uhi_pip_core as core
    from map_viewer import MapViewer

SOURCES = [
    ("Automatic - try all options", "auto"),
    ("Folder on my computer", "local"),
    ("AWS account (small download fee)", "aws_s3"),
    ("USGS website login", "usgs_https"),
]


class App:
    def __init__(self, root):
        self.root = root
        root.setWindowTitle("Urban Heat Island - Landsat 10/11")
        root.setFixedSize(560, 640)
        if os.path.exists(os.path.join(HERE, "icon.png")):
            root.setWindowIcon(QIcon(os.path.join(HERE, "icon.png")))

        self.queue = queue.Queue()
        self.worker = None
        self.cancel_event = threading.Event()
        self.last_output_dir = None
        self.last_result = None

        layout = QVBoxLayout(root)
        title = QLabel("Make an urban heat island map from a satellite image")
        title.setStyleSheet("font-size: 13px; font-weight: bold;")
        layout.addWidget(title)

        form = QFormLayout()
        self.date_var = self._line(form, "Date (YYYY-MM-DD)",
                                   (date.today() - timedelta(days=30)).isoformat())
        self.place_var = self._line(form, "Location name", "Milan")
        self.lat_var = self._line(form, "Latitude of centre", "45.4642")
        self.lon_var = self._line(form, "Longitude of centre", "9.1900")
        self.radius_var = self._line(form, "Radius in km", "20")
        self.output_var = self._line(form, "Save maps in",
                                     os.path.expanduser("~/uhi_output"), browse="folder")
        self.local_var = self._line(form, "Folder with satellite files (optional)", "",
                                    browse="folder")
        self.source_combo = QComboBox()
        self.source_combo.addItems([label for label, _ in SOURCES])
        form.addRow("Where to get the data", self.source_combo)
        self.cookie_var = self._line(form, "USGS login cookie (optional)", "",
                                     password=True)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.run_button = QPushButton("Make the maps")
        self.run_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        self.open_button = QPushButton("Open output folder")
        self.open_button.clicked.connect(self.open_output)
        self.open_button.setEnabled(False)
        self.dates_button = QPushButton("Dates with images")
        self.dates_button.setToolTip("List dates with satellite images within 60 days")
        self.dates_button.clicked.connect(self.list_dates)
        self.view_button = QPushButton("View maps")
        self.view_button.clicked.connect(self.view_maps)
        self.view_button.setEnabled(False)
        for button in (self.run_button, self.cancel_button, self.open_button,
                       self.dates_button, self.view_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        self.status = QLabel("Fill in the form and click 'Make the maps'.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        layout.addWidget(self.result)

        note = QLabel("Blank areas on the maps are cloud or water.")
        note.setStyleSheet("color: #666;")
        layout.addWidget(note)

    def _line(self, form, label, value, browse=None, password=False):
        if browse:
            row = QWidget()
            fields = QHBoxLayout(row)
            field = QLineEdit(value)
            button = QPushButton("...")
            button.setFixedWidth(32)
            button.clicked.connect(lambda: self._pick(field))
            fields.addWidget(field)
            fields.addWidget(button)
            fields.setContentsMargins(0, 0, 0, 0)
            form.addRow(label, row)
        else:
            field = QLineEdit(value)
            if password:
                field.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow(label, field)
        return field

    def _pick(self, field):
        chosen = QFileDialog.getExistingDirectory(
            self.root, "Choose a folder", field.text() or os.path.expanduser("~"))
        if chosen:
            field.setText(chosen)

    def _params(self):
        try:
            day = datetime.strptime(self.date_var.text().strip(), "%Y-%m-%d").date()
            lat = float(self.lat_var.text())
            lon = float(self.lon_var.text())
            radius = float(self.radius_var.text())
        except ValueError:
            raise ValueError("Check the date (YYYY-MM-DD) and the latitude, longitude "
                             "and radius numbers.")
        if radius <= 0:
            raise ValueError("Radius must be greater than zero.")
        label = SOURCES[self.source_combo.currentIndex()][1]
        return {
            "day": day.isoformat(),
            "location": self.place_var.text().strip() or "area",
            "lat": lat, "lon": lon, "radius_km": radius,
            "output_dir": self.output_var.text().strip() or os.path.expanduser("~/uhi_output"),
            "local_dir": self.local_var.text().strip(),
            "source": label,
            "cookie": self.cookie_var.text().strip(),
        }

    def start(self):
        try:
            params = self._params()
        except ValueError as exc:
            QMessageBox.critical(self.root, "Check your entries", str(exc))
            return
        self.cancel_event.clear()
        self.queue = queue.Queue()
        self.progress.setValue(0)
        self.status.setText("Looking for a satellite image...")
        self.result.clear()
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.open_button.setEnabled(False)
        self.worker = threading.Thread(target=self._work, args=(params,), daemon=True)
        self.worker.start()
        QTimer.singleShot(100, self._poll)

    def list_dates(self):
        try:
            params = self._params()
        except ValueError as exc:
            QMessageBox.critical(self.root, "Check your entries", str(exc))
            return
        self.worker = threading.Thread(target=self._dates_worker,
                                       args=(params,), daemon=True)
        self.worker.start()
        self.status.setText("Looking for dates with images...")
        QTimer.singleShot(100, self._poll)

    def _dates_worker(self, params):
        try:
            self.queue.put(("dates", core.list_dates(params)))
        except Exception as exc:
            self.queue.put(("error", "{}: {}".format(type(exc).__name__, exc)))

    def _show_dates(self, dates):
        if not dates:
            QMessageBox.information(self.root, "Dates with images",
                                    "No Landsat images found within 60 days.")
            return
        dialog = QDialog(self.root)
        dialog.setWindowTitle("Dates with images (within 60 days)")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Pick a date to use it in the form:"))
        listing = QListWidget()
        for iso, cloud, scene, catalog in dates:
            listing.addItem("{}  —  {:.0f}% cloud  —  {}  ({})".format(
                iso, cloud, scene, catalog))
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def choose(item=None):
            row = listing.currentRow()
            if row >= 0:
                self.date_var.setText(dates[row][0])
            dialog.accept()

        listing.itemDoubleClicked.connect(choose)
        buttons.button(QDialogButtonBox.StandardButton.Ok).clicked.connect(choose)
        dialog.exec()

    def _work(self, params):
        def progress(value, message):
            self.queue.put(("progress", value, message))
        try:
            result = core.compute(params, progress, self.cancel_event.is_set)
            self.queue.put(("done", result))
        except core.Cancelled:
            self.queue.put(("cancelled", None))
        except Exception as exc:
            message = "{}: {}".format(type(exc).__name__, exc)
            if "Could not find a suitable satellite image" in message:
                try:
                    dates = core.list_dates(params)
                    if dates:
                        message += ("\n\nDates with images within 60 days:\n"
                                    + "\n".join("{} ({:.0f}% cloud)".format(d, c)
                                                for d, c, _, _ in dates))
                except Exception:
                    pass
            self.queue.put(("error", message))

    def _poll(self):
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            kind, payload = item[0], item[1]
            if kind == "progress":
                self.progress.setValue(payload)
                self.status.setText(item[2])
            elif kind == "done":
                stats = payload["stats"]
                self.last_output_dir = os.path.dirname(payload["lst_path"])
                self.progress.setValue(100)
                self.status.setText("Done. Maps saved in {}.".format(self.last_output_dir))
                text = ("Data source: {}\nAverage temperature: {:.1f} °C\n"
                        "Temperature range: {:.1f} to {:.1f} °C\n"
                        "Clear-sky pixels: {}\n\nTemperature map: {}\nHeat-zone map: {}"
                        .format(payload["source"], stats["mean"], stats["min"],
                                stats["max"], stats["valid_pixels"],
                                payload["lst_path"], payload["uhi_path"]))
                self.result.setPlainText(text)
                self.open_button.setEnabled(True)
                self.view_button.setEnabled(True)
                self.last_result = payload
                answer = QMessageBox.question(
                    self.root, "Finished",
                    "Your two maps are ready. Open the map viewer?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if answer == QMessageBox.StandardButton.Yes:
                    self.view_maps()
            elif kind == "error":
                self.status.setText("Could not finish: {}".format(payload))
                QMessageBox.critical(self.root, "Something went wrong", payload)
            elif kind == "dates":
                self._show_dates(payload)
                self.status.setText("Pick a date from the list, or keep the current one.")
            elif kind == "cancelled":
                self.status.setText("Cancelled.")
                self.progress.setValue(0)
        if self.worker and self.worker.is_alive():
            QTimer.singleShot(100, self._poll)
        else:
            self.run_button.setEnabled(True)
            self.cancel_button.setEnabled(False)

    def cancel(self):
        self.cancel_event.set()
        self.status.setText("Stopping after the current step...")

    def open_output(self):
        path = self.last_output_dir
        if path and os.path.isdir(path):
            try:
                if sys.platform.startswith("win"):
                    os.startfile(path)
                elif sys.platform == "darwin":
                    subprocess.run(["open", path], check=False)
                else:
                    subprocess.run(["xdg-open", path], check=False)
            except OSError:
                QMessageBox.information(self.root, "Output folder", path)

    def view_maps(self):
        if self.last_result:
            MapViewer(self.last_result, self.root).exec()
        else:
            QMessageBox.information(self.root, "View maps",
                                    "Make the maps first, then view them here.")


def main():
    app = QApplication(sys.argv)
    window = QWidget()
    App(window)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
