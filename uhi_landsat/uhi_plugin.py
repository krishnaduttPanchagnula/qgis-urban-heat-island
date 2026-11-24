import os
import tempfile
import traceback

from qgis.PyQt.QtGui import QAction, QColor, QIcon
from qgis.core import (
    Qgis, QgsApplication, QgsColorRampShader, QgsPalettedRasterRenderer,
    QgsProject, QgsRasterLayer, QgsRasterShader, QgsSingleBandPseudoColorRenderer,
    QgsTask,
)

from .uhi_core import Cancelled, CLASS_LABELS, compute
from .uhi_dialog import UhiDialog

LST_COLORS = [(0, QColor("#2b3a67")), (12, QColor("#3b82b8")),
              (22, QColor("#8dc6a8")), (30, QColor("#e8c55a")),
              (40, QColor("#e2703a")), (55, QColor("#8b1a1a"))]
UHI_COLORS = [QColor("#4575b4"), QColor("#91bfdb"), QColor("#e0f3f8"),
              QColor("#fc8d59"), QColor("#d73027")]


class UhiTask(QgsTask):
    def __init__(self, params, iface):
        super().__init__("Fetching Landsat and computing UHI", QgsTask.CanCancel)
        self.params = params
        self.iface = iface
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = compute(self.params, self.setProgress, self.isCancelled)
            return self.result is not None
        except Cancelled:
            self.error = "Cancelled"
            return False
        except Exception as exc:  # surfaced in finished(), not swallowed
            self.error = "{}: {}".format(type(exc).__name__, exc)
            log = os.path.join(tempfile.gettempdir(), "uhi_plugin_error.log")
            with open(log, "a") as handle:
                handle.write(traceback.format_exc() + "\n")
            return False

    def finished(self, success):
        bar = self.iface.messageBar()
        if not success:
            level = Qgis.Info if self.error == "Cancelled" else Qgis.Critical
            bar.pushMessage("Urban heat island", self.error or "Failed", level, 10)
            return
        stats = self.result["stats"]
        self._add_layer(self.result["lst_path"], "LST {} °C".format(self.params["location"]),
                        "lst")
        self._add_layer(self.result["uhi_path"], "UHI {}".format(self.params["location"]),
                        "uhi")
        bar.pushMessage(
            "Urban heat island",
            "Source: {} | mean {:.1f} °C, range {:.1f}-{:.1f} °C ({} clear-sky pixels)".format(
                self.result["source"], stats["mean"], stats["min"], stats["max"],
                stats["valid_pixels"]),
            Qgis.Success, 8,
        )

    def _add_layer(self, path, name, kind):
        layer = QgsRasterLayer(path, name)
        if not layer.isValid():
            self.iface.messageBar().pushMessage("Urban heat island",
                                                "Could not load {}".format(path), Qgis.Warning)
            return
        if kind == "lst":
            values = [item[0] for item in LST_COLORS]
            ramp = QgsColorRampShader(values[0], values[-1])
            ramp.setColorRampType(QgsColorRampShader.Interpolated)
            ramp.setColorRampItemList([
                QgsColorRampShader.ColorRampItem(value, color, "{:.0f} °C".format(value))
                for value, color in LST_COLORS
            ])
            shader = QgsRasterShader()
            shader.setRasterShaderFunction(ramp)
            layer.setRenderer(QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader))
        else:
            classes = [QgsPalettedRasterRenderer.Class(index, UHI_COLORS[index - 1], label)
                       for index, label in CLASS_LABELS.items()]
            layer.setRenderer(QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes))
        QgsProject.instance().addMapLayer(layer)


class UhiPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None

    def initGui(self):
        self.action = QAction(QIcon(), "Urban Heat Island (Landsat 10/11)",
                              self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Urban Heat Island", self.action)

    def unload(self):
        self.iface.removePluginMenu("&Urban Heat Island", self.action)
        self.iface.removeToolBarIcon(self.action)
        del self.action

    def run(self):
        dialog = UhiDialog(self.iface.mainWindow())
        if dialog.exec_() != UhiDialog.Accepted:
            return
        QgsApplication.taskManager().addTask(UhiTask(dialog.params(), self.iface))
