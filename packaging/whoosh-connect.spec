# PyInstaller spec: `make app` -> dist/whoosh-connect.app (onedir; see issue #1).
# Not under build/: that directory is PyInstaller's (ignored) work dir.
import os
import sysconfig
from importlib.metadata import version

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
VERSION = version("whoosh-connect")
# The bundle can't run on an older macOS than the Python it carries (Homebrew's 3.15: 26.0,
# arm64 only). Build with a python.org universal2 Python to reach older Macs.
MIN_MACOS = sysconfig.get_config_var("MACOSX_DEPLOYMENT_TARGET") or "11.0"

# bleak imports its platform backend inside functions; collect it explicitly.
hidden = collect_submodules("bleak.backends.corebluetooth") + ["CoreBluetooth", "libdispatch"]

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[os.path.join(ROOT, "src")],
    hiddenimports=hidden,
    datas=copy_metadata("whoosh-connect"),
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="whoosh-connect",
    console=False,
    argv_emulation=False,
    codesign_identity=None,  # ad-hoc; Developer ID signing is issue #5
)
coll = COLLECT(exe, a.binaries, a.datas, name="whoosh-connect")
app = BUNDLE(
    coll,
    name="whoosh-connect.app",
    bundle_identifier="com.tusharpandey13.whoosh-connect",
    version=VERSION,
    info_plist={
        "CFBundleName": "whoosh-connect",
        "CFBundleDisplayName": "whoosh-connect",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": MIN_MACOS,
        "NSHighResolutionCapable": True,
    },
)
