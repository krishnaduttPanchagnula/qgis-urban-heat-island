"""Smoke + worker test for the desktop app (needs GDAL, numpy and Tk)."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "desktop_app"))
sys.path.insert(0, HERE)

import tkinter as tk  # noqa: E402

import uhi_desktop  # noqa: E402
from test_integration import build_scene  # noqa: E402


def test_desktop_worker():
    root = tk.Tk()
    root.withdraw()
    try:
        app = uhi_desktop.App(root)
        assert app._params()["source"] == "auto"
        assert app._params()["radius_km"] == 20.0

        with tempfile.TemporaryDirectory() as tmp:
            bands, item, bbox = build_scene(os.path.join(tmp, "bands"))
            original = uhi_desktop.core.find_scene
            uhi_desktop.core.find_scene = lambda *a, **k: (bbox, item)
            try:
                params = {
                    "day": "2023-07-29", "location": "TestCity",
                    "lat": 45.0, "lon": 9.0, "radius_km": 20,
                    "output_dir": os.path.join(tmp, "out"), "local_dir": bands,
                    "source": "local", "cookie": "",
                }
                app._work(params)
            finally:
                uhi_desktop.core.find_scene = original

            messages = []
            while True:
                try:
                    messages.append(app.queue.get_nowait())
                except Exception:
                    break
            kinds = [m[0] for m in messages]
            assert "done" in kinds, messages
            result = [m[1] for m in messages if m[0] == "done"][0]
            assert result["stats"]["valid_pixels"] > 500000
            assert os.path.exists(result["lst_path"])
            assert any(m[0] == "progress" and m[1] == 100 for m in messages)
            print("ok desktop_worker: {} messages, mean {:.2f} C".format(
                len(messages), result["stats"]["mean"]))
    finally:
        root.destroy()


if __name__ == "__main__":
    test_desktop_worker()
