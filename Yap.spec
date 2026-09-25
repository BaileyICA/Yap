# PyInstaller build recipe for the portable Windows application.
from PyInstaller.utils.hooks import collect_all

datas = [("assets/yap-logo.png", "assets")]
binaries = []
hiddenimports = ["PIL._tkinter_finder", "sounddevice", "soundcard"]

# These libraries load native extensions and model support dynamically.
for package in (
    "ctranslate2",
    "faster_whisper",
    "onnx_asr",
    "onnxruntime",
    "nvidia.cublas",
    "nvidia.cuda_nvrtc",
    "nvidia.cuda_runtime",
    "nvidia.cudnn",
    "nvidia.cufft",
    "nvidia.curand",
    "soundcard",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "IPython", "jupyter", "matplotlib", "torch"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Yap",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/yap.ico",
)
