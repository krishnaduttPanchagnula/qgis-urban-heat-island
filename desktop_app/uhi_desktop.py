"""Standalone desktop app for the urban heat island workflow.

Uses the exact same uhi_core engine as the QGIS plugin, so results match.
Only requirements: Python 3, numpy and GDAL Python bindings.
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "uhi_landsat"))
import uhi_core as core  # noqa: E402

SOURCES = [
    ("Automatic - try all options", "auto"),
    ("Folder on my computer", "local"),
    ("AWS account (small download fee)", "aws_s3"),
    ("USGS website login", "usgs_https"),
]


class App:
    def __init__(self, root):
        self.root = root
        root.title("Urban Heat Island - Landsat 10/11")
        root.resizable(False, False)
        icon_path = os.path.join(HERE, "icon.png")
        if os.path.exists(icon_path):
            self.icon = tk.PhotoImage(file=icon_path)
            root.iconphoto(True, self.icon)

        self.queue = queue.Queue()
        self.worker = None
        self.cancel_event = threading.Event()
        self.last_output_dir = None

        main = ttk.Frame(root, padding=14)
        main.grid(row=0, column=0, sticky="nsew")

        ttk.Label(main, text="Make an urban heat island map from a satellite image",
                  font=("DejaVu Sans", 12, "bold")).grid(row=0, column=0, columnspan=3,
                                                         sticky="w", pady=(0, 10))

        row = 1
        self.date_var = tk.StringVar(value=(date.today() - timedelta(days=30)).isoformat())
        row = self._entry(main, row, "Date (YYYY-MM-DD)", self.date_var)

        self.place_var = tk.StringVar(value="Milan")
        row = self._entry(main, row, "Location name", self.place_var)

        self.lat_var = tk.StringVar(value="45.4642")
        row = self._entry(main, row, "Latitude of centre", self.lat_var)

        self.lon_var = tk.StringVar(value="9.1900")
        row = self._entry(main, row, "Longitude of centre", self.lon_var)

        self.radius_var = tk.StringVar(value="20")
        row = self._entry(main, row, "Radius in km", self.radius_var)

        self.output_var = tk.StringVar(value=os.path.expanduser("~/uhi_output"))
        row = self._entry(main, row, "Save maps in", self.output_var, browse="folder")

        self.local_var = tk.StringVar()
        row = self._entry(main, row, "Folder with satellite files (optional)",
                          self.local_var, browse="folder")

        ttk.Label(main, text="Where to get the data").grid(row=row, column=0, sticky="w", pady=4)
        self.source_combo = ttk.Combobox(main, state="readonly",
                                         values=[label for label, _ in SOURCES], width=34)
        self.source_combo.current(0)
        self.source_combo.grid(row=row, column=1, columnspan=2, sticky="w", pady=4)
        row += 1

        self.cookie_var = tk.StringVar()
        self._entry(main, row, "USGS login cookie (optional)", self.cookie_var, show="*")
        row += 1

        buttons = ttk.Frame(main)
        buttons.grid(row=row, column=0, columnspan=3, pady=(12, 6), sticky="w")
        self.run_button = ttk.Button(buttons, text="Make the maps", command=self.start)
        self.run_button.pack(side="left")
        self.cancel_button = ttk.Button(buttons, text="Cancel", command=self.cancel,
                                        state="disabled")
        self.cancel_button.pack(side="left", padx=6)
        self.open_button = ttk.Button(buttons, text="Open output folder",
                                      command=self.open_output, state="disabled")
        self.open_button.pack(side="left")
        row += 1

        self.progress = ttk.Progressbar(main, mode="determinate", length=410)
        self.progress.grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 4))
        row += 1

        self.status = tk.StringVar(value="Fill in the form and click 'Make the maps'.")
        ttk.Label(main, textvariable=self.status, foreground="#333333",
                  wraplength=430, justify="left").grid(row=row, column=0, columnspan=3,
                                                       sticky="w", pady=(0, 8))
        row += 1

        self.result = tk.Text(main, height=6, width=62, state="disabled")
        self.result.grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1
        ttk.Label(main, text="Blank areas on the maps are cloud or water.",
                  foreground="#666666").grid(row=row, column=0, columnspan=3, sticky="w",
                                             pady=(6, 0))

    def _entry(self, parent, row, label, variable, browse=None, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, textvariable=variable, width=42, show=show)
        entry.grid(row=row, column=1, columnspan=2, sticky="w", pady=4)
        if browse:
            ttk.Button(parent, text="...", width=3,
                       command=lambda: self._pick(variable)).grid(row=row, column=3, padx=(4, 0))
        return row + 1

    def _pick(self, variable):
        chosen = filedialog.askdirectory(initialdir=variable.get() or os.path.expanduser("~"))
        if chosen:
            variable.set(chosen)

    def _params(self):
        try:
            day = datetime.strptime(self.date_var.get().strip(), "%Y-%m-%d").date()
            lat = float(self.lat_var.get())
            lon = float(self.lon_var.get())
            radius = float(self.radius_var.get())
        except ValueError:
            raise ValueError("Check the date (YYYY-MM-DD) and the latitude, longitude "
                             "and radius numbers.")
        if radius <= 0:
            raise ValueError("Radius must be greater than zero.")
        label = SOURCES[self.source_combo.current()][1]
        return {
            "day": day.isoformat(),
            "location": self.place_var.get().strip() or "area",
            "lat": lat, "lon": lon, "radius_km": radius,
            "output_dir": self.output_var.get().strip() or os.path.expanduser("~/uhi_output"),
            "local_dir": self.local_var.get().strip(),
            "source": label,
            "cookie": self.cookie_var.get().strip(),
        }

    def start(self):
        try:
            params = self._params()
        except ValueError as exc:
            messagebox.showerror("Check your entries", str(exc))
            return
        self.cancel_event.clear()
        self.queue = queue.Queue()
        self.progress["value"] = 0
        self.status.set("Looking for a satellite image...")
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.configure(state="disabled")
        self.run_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.open_button.configure(state="disabled")
        self.worker = threading.Thread(target=self._work, args=(params,), daemon=True)
        self.worker.start()
        self.root.after(100, self._poll)

    def _work(self, params):
        def progress(value, message):
            self.queue.put(("progress", value, message))
        try:
            result = core.compute(params, progress, self.cancel_event.is_set)
            self.queue.put(("done", result))
        except core.Cancelled:
            self.queue.put(("cancelled", None))
        except Exception as exc:
            self.queue.put(("error", "{}: {}".format(type(exc).__name__, exc)))

    def _poll(self):
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            kind, payload = item[0], item[1]
            if kind == "progress":
                self.progress["value"] = payload
                self.status.set(item[2])
            elif kind == "done":
                stats = payload["stats"]
                self.last_output_dir = os.path.dirname(payload["lst_path"])
                self.progress["value"] = 100
                self.status.set("Done. Maps saved in {}.".format(self.last_output_dir))
                text = ("Data source: {}\nAverage temperature: {:.1f} °C\n"
                        "Temperature range: {:.1f} to {:.1f} °C\n"
                        "Clear-sky pixels: {}\n\nTemperature map: {}\nHeat-zone map: {}"
                        .format(payload["source"], stats["mean"], stats["min"],
                                stats["max"], stats["valid_pixels"],
                                payload["lst_path"], payload["uhi_path"]))
                self._show_result(text)
                self.open_button.configure(state="normal")
                messagebox.showinfo("Finished", "Your two maps are ready.")
            elif kind == "error":
                self.status.set("Could not finish: {}".format(payload))
                messagebox.showerror("Something went wrong", payload)
            elif kind == "cancelled":
                self.status.set("Cancelled.")
                self.progress["value"] = 0
        if self.worker and self.worker.is_alive():
            self.root.after(100, self._poll)
        else:
            self.run_button.configure(state="normal")
            self.cancel_button.configure(state="disabled")

    def _show_result(self, text):
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("1.0", text)
        self.result.configure(state="disabled")

    def cancel(self):
        self.cancel_event.set()
        self.status.set("Stopping after the current step...")

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
                messagebox.showinfo("Output folder", path)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
