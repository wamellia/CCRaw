# macOS Apple silicon build: ONNX Runtime with Core ML, PyObjC Metal and the
# pure-Perl ExifTool.  Produces "dist/CCRaw.app"; run it through tools/build_macos.sh,
# which draws build/macos/CCRaw.icns first and signs the bundle afterwards.
import os
import re
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

root = Path(SPECPATH)
version = re.search(r"__version__ = '([0-9.]+)'",
                    (root / 'ccraw' / '__init__.py').read_text(encoding='utf-8')).group(1)
art = root / 'build' / 'macos'
minimum = os.environ.get('CCRAW_MACOS_MINIMUM', '15.0')

raw_data, raw_binaries, raw_hidden = collect_all('rawpy')
ort_data, ort_binaries, ort_hidden = collect_all('onnxruntime')

# Windows-only runtime files (exiftool.exe with its Strawberry Perl) are not shipped on macOS;
# the Perl ExifTool from tools/fetch_exiftool.py runs with /usr/bin/perl instead.
assets = []
for item in sorted((root / 'assets').iterdir()):
    if item.name == 'exiftool':
        assets.append((str(item / 'unix'), 'assets/exiftool/unix'))
    elif item.is_dir():
        assets.append((str(item), f'assets/{item.name}'))
    else:
        assets.append((str(item), 'assets'))

a = Analysis(
    [str(root / 'main.py')], pathex=[str(root)],
    binaries=raw_binaries + ort_binaries,
    datas=assets + [(str(root / 'LICENSE'), '.'), (str(root / 'NOTICE'), '.'), (str(root / 'THIRD_PARTY.md'), '.'), (str(root / 'licenses'), 'licenses')] + raw_data + ort_data + collect_data_files('tifffile') + collect_data_files('ccraw', includes=['resources/*']),
    hiddenimports=raw_hidden + ort_hidden + ['PIL.ImageCms', 'objc', 'Foundation', 'Metal', 'PySide6.QtMultimedia', 'numexpr', 'numba', 'llvmlite'],
    excludes=['cupy', 'torch', 'torchvision', 'onnx', 'windowsml', 'tkinter'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='CCRaw',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False, argv_emulation=False,
          target_arch='arm64', codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='CCRaw')

document = lambda name, types, rank='Alternate': {
    'CFBundleTypeName': name, 'CFBundleTypeRole': 'Editor', 'LSHandlerRank': rank, 'LSItemContentTypes': types}
exported = lambda identifier, description, extension: {
    'UTTypeIdentifier': identifier, 'UTTypeDescription': description, 'UTTypeConformsTo': ['public.json'],
    'UTTypeIconFile': 'CCRaw.icns', 'UTTypeTagSpecification': {'public.filename-extension': [extension]}}
app = BUNDLE(
    coll, name='CCRaw.app', icon=str(art / 'CCRaw.icns'),
    bundle_identifier='org.ccraw.desktop', version=version,
    info_plist={
        'CFBundleName': 'CCRaw',
        'CFBundleDisplayName': 'CCRaw',
        'CFBundleShortVersionString': version,
        'CFBundleVersion': version,
        'CFBundleDevelopmentRegion': 'zh_CN',
        'CFBundleLocalizations': ['zh_CN', 'en'],
        'CFBundleAllowMixedLocalizations': True,
        'LSMinimumSystemVersion': minimum,
        'LSArchitecturePriority': ['arm64'],
        'LSApplicationCategoryType': 'public.app-category.photography',
        'NSHighResolutionCapable': True,
        'NSHumanReadableCopyright': 'CCRaw · MIT License',
        # Voice instructions are recognized offline on this Mac.
        'NSMicrophoneUsageDescription': 'CCRaw 在本机离线识别你的语音修图指令，录音不会上传。',
        # Every RAW type macOS knows (ARW, CR2 / CR3, NEF, RAF, RW2, DNG, ...) conforms to public.camera-raw-image.
        'CFBundleDocumentTypes': [
            document('Camera RAW', ['public.camera-raw-image']),
            document('Image', ['public.jpeg', 'public.png', 'public.tiff']),
            document('CCRaw 工程', ['org.ccraw.desktop.project'], 'Owner'),
            document('CCRaw 选片集', ['org.ccraw.desktop.album'], 'Owner'),
            document('Legacy Lumen project', ['org.ccraw.legacy.project']),
            document('Legacy Lumen album', ['org.ccraw.legacy.album']),
        ],
        'UTExportedTypeDeclarations': [
            exported('org.ccraw.desktop.project', 'CCRaw 工程', 'ccraw'),
            exported('org.ccraw.desktop.album', 'CCRaw 选片集', 'ccrawalbum'),
        ],
        'UTImportedTypeDeclarations': [
            exported('org.ccraw.legacy.project', 'Legacy Lumen project', 'lumen'),
            exported('org.ccraw.legacy.album', 'Legacy Lumen album', 'lumenalbum'),
        ],
    },
)
