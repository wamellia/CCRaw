"""A pipeline margin must not shift or recolor a wide-gamut photograph."""

import numpy as np
import tifffile


def test_portrait_pipeline_margin_wide_export_restores_original_pixels(tmp_path):
    from ccraw import export_presets, watermark

    image = np.linspace(0.03, 0.83, 20 * 8 * 3, dtype=np.float32).reshape(20, 8, 3)
    settings = dict(
        watermark.defaults(),
        enabled=True,
        pipeline=[dict(processor_name='margin', top_margin=3, left_margin=3, margin_color='white')],
    )
    path = tmp_path / 'portrait.tif'
    export_presets.export_rgb(
        path,
        image,
        {'format': 'tiff', 'bit_depth': 16, 'color_space': 'display_p3', 'watermark': settings},
    )
    result = tifffile.imread(path)
    pad = 3
    expected = np.round(image * 65535).astype(np.uint16)
    np.testing.assert_array_equal(result[pad : pad + 20, pad : pad + 8], expected)
