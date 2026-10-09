"""Create the macOS icon and DMG background from CCRaw's vector artwork."""

from pathlib import Path
from PIL import Image, ImageDraw
from make_brand_assets import main as make_brand

ROOT = Path(__file__).resolve().parents[1]


def main():
    make_brand()
    output = ROOT / 'build/macos'
    output.mkdir(parents=True, exist_ok=True)
    with Image.open(ROOT / 'assets/ccraw.png') as source:
        source.save(output / 'CCRaw.icns')
    background = Image.new('RGB', (640, 420), '#F5F5F7')
    draw = ImageDraw.Draw(background)
    draw.text((245, 48), 'CCRaw', fill='#323234')
    draw.text((180, 355), 'Drag CCRaw to Applications', fill='#737375')
    background.save(output / 'dmg-background.png')
    background.resize((1280, 840), Image.Resampling.LANCZOS).save(output / 'dmg-background@2x.png')


if __name__ == '__main__':
    main()
