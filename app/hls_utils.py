import os
import shutil
from typing import List

def mkdir_p(path: str):
    os.makedirs(path, exist_ok=True)

def rmrf(path: str):
    if os.path.exists(path):
        shutil.rmtree(path)

def atomic_write_text(path: str, text: str):
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.rename(path + ".tmp", path)

def hardlink_or_copy(src: str, dst: str):
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
