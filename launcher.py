"""Entry point for the packaged app; PyInstaller runs this as a plain script, not as part of the package."""
import os
import sys

# A windowed exe has no console, so sys.stdout/stderr are None and anything that prints
# (e.g. Hugging Face download progress bars) crashes with "'NoneType' has no attribute 'write'".
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from yap.app import main  # noqa: E402

main()
