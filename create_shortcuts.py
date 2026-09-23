"""Create the Yap app icon plus Start menu and Desktop shortcuts.

Run with:  .venv\\Scripts\\python create_shortcuts.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from yap.branding import BRAND_NAVY, icon  # noqa: E402

ASSETS = os.path.join(ROOT, "assets")
ICO = os.path.join(ASSETS, "yap.ico")
PYW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")

os.makedirs(ASSETS, exist_ok=True)
img = icon(BRAND_NAVY, 256)
img.save(ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

ps = f"""
$sh = New-Object -ComObject WScript.Shell
$targets = @(
  (Join-Path $sh.SpecialFolders('Programs') 'Yap.lnk'),
  (Join-Path $sh.SpecialFolders('Desktop') 'Yap.lnk')
)
foreach ($t in $targets) {{
  $s = $sh.CreateShortcut($t)
  $s.TargetPath = '{PYW}'
  $s.Arguments = '-m yap'
  $s.WorkingDirectory = '{ROOT}'
  $s.IconLocation = '{ICO}'
  $s.Description = 'Yap - voice dictation and meeting notes'
  $s.WindowStyle = 7
  $s.Save()
  Write-Output $t
}}
"""
out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
print(out.stdout.strip() or out.stderr.strip())
