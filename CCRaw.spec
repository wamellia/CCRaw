# Windows x64 build; collects the active ONNX Runtime and optional Windows ML components.
from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)
from ccraw import __version__

root = Path(SPECPATH)
raw_data, raw_binaries, raw_hidden = collect_all('rawpy')
ort_data, ort_binaries, ort_hidden = collect_all('onnxruntime')
agent_data, agent_binaries, agent_hidden = collect_all('nanobot')
tokenizer_data, tokenizer_binaries, tokenizer_hidden = collect_all('tokenizers')
try:
    winml_data, winml_binaries, winml_hidden = collect_all('windowsml')
except Exception:
    winml_data, winml_binaries, winml_hidden = [], [], []
a = Analysis(
    [str(root / 'main.py')], pathex=[str(root)],
    binaries=raw_binaries + ort_binaries + winml_binaries + agent_binaries + tokenizer_binaries,
    datas=[(str(root / 'assets'), 'assets'), (str(root / 'LICENSE'), '.'), (str(root / 'NOTICE'), '.'), (str(root / 'THIRD_PARTY.md'), '.'), (str(root / 'licenses'), 'licenses')] + raw_data + ort_data + winml_data + agent_data + tokenizer_data + copy_metadata('nanobot-ai', recursive=True) + collect_data_files('tifffile') + collect_data_files('ccraw', includes=['resources/**/*']),
    hiddenimports=raw_hidden + ort_hidden + winml_hidden + agent_hidden + tokenizer_hidden + ['PIL.ImageCms', 'PySide6.QtMultimedia', 'numexpr', 'numba', 'llvmlite', 'tiktoken_ext.openai_public'],
    excludes=['cupy', 'torch', 'torchvision', 'onnx', 'tkinter'], noarchive=False,
)
if os.name == 'nt':
    # Qt 6.11 uses the Windows ICU shim. A different ICU on PATH must not shadow
    # System32/icuuc.dll: it exports versioned symbols and prevents Qt loading.
    a.binaries = [item for item in a.binaries
                  if Path(item[0]).name.lower() != 'icuuc.dll'
                  and not Path(item[0]).name.lower().startswith('icudt')]
pyz = PYZ(a.pure)
version_numbers = tuple(int(part) for part in __version__.split('.')) + (0,)
windows_version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_numbers, prodvers=version_numbers,
                     mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName', 'CCRaw'),
        StringStruct('FileDescription', 'CCRaw photo editor and Photo Agent'),
        StringStruct('FileVersion', __version__),
        StringStruct('InternalName', 'CCRaw'),
        StringStruct('OriginalFilename', 'CCRaw.exe'),
        StringStruct('ProductName', 'CCRaw-Pro'),
        StringStruct('ProductVersion', __version__),
        StringStruct('LegalCopyright', 'Copyright (c) 2026 CCRaw contributors'),
    ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])],
)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='CCRaw',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False, icon=str(root/'assets/ccraw.ico'),
          version=windows_version)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='CCRaw')
