"""Versão distribuída com o código-fonte e com o executável."""

import re
import sys
from pathlib import Path


RESOURCE_ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3]


def load_version(root=RESOURCE_ROOT):
    version = (root / "VERSION").read_text(encoding="ascii").strip()
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        raise ValueError("VERSION deve conter uma versão MAJOR.MINOR.PATCH.")
    return version


VERSION = load_version()


def windows_resource(version):
    parts = tuple(map(int, version.split("."))) + (0,)
    if any(part > 65535 for part in parts):
        raise ValueError("Os componentes do recurso Windows devem caber em 16 bits.")
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={parts}, prodvers={parts}, mask=0x3f,
                    flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'ChatProbe'),
      StringStruct('ProductName', 'ChatProbe'),
      StringStruct('FileVersion', {version!r}),
      StringStruct('ProductVersion', {version!r})
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


if __name__ == "__main__":
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(windows_resource(VERSION), encoding="utf-8")
    else:
        print(VERSION)
