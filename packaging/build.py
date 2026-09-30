"""
Builds the standalone AlphaNest app for the OS this runs on (PyInstaller
can't cross-compile: build the Windows .exe on Windows, the .app on a Mac --
.github/workflows/build-app.yml does all three on GitHub's runners).

    pip install -r requirements.txt -r packaging/requirements-build.txt
    python packaging/build.py

Result in dist/: AlphaNest.exe (Windows), AlphaNest (Linux) or
AlphaNest.app (macOS).
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_DIR = os.path.join(ROOT, "build")


def render_icon():
    """The app's alpha badge (native_app/assets/alpha-badge.svg) as a 512px
    PNG, converted to the platform's icon format -- PyInstaller (via Pillow)
    reads .ico/.icns, not SVG."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtGui, QtSvg

    app = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])  # noqa: F841
    renderer = QtSvg.QSvgRenderer(os.path.join(ROOT, "native_app", "assets", "alpha-badge.svg"))
    image = QtGui.QImage(512, 512, QtGui.QImage.Format_ARGB32)
    image.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(image)
    renderer.render(painter)
    painter.end()

    os.makedirs(BUILD_DIR, exist_ok=True)
    png = os.path.join(BUILD_DIR, "icon.png")
    image.save(png)

    from PIL import Image

    for stale in ("icon.ico", "icon.icns"):
        if os.path.exists(os.path.join(BUILD_DIR, stale)):
            os.remove(os.path.join(BUILD_DIR, stale))
    if sys.platform == "win32":
        Image.open(png).save(os.path.join(BUILD_DIR, "icon.ico"),
                             sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    elif sys.platform == "darwin":
        Image.open(png).save(os.path.join(BUILD_DIR, "icon.icns"))


def main():
    render_icon()
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", os.path.join(ROOT, "dist"),
         "--workpath", os.path.join(BUILD_DIR, "pyinstaller"),
         os.path.join(ROOT, "packaging", "alphanest.spec")],
        check=True, cwd=ROOT,
    )
    print("\nBuilt:", os.path.join(ROOT, "dist"))


if __name__ == "__main__":
    main()
