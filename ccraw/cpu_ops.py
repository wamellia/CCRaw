"""Float32 CPU expression fusion; bounded threads and a NumPy fallback.

NumExpr evaluates small blocks in native threads instead of allocating a full
image for every arithmetic operator. The same transfer function and precision
are used in interactive, region and export rendering.
"""

import numpy as np
from . import performance, native_kernels

try:
    import numexpr as ne

    # Image jobs are serialized. Eight compute threads leave Qt room to paint;
    # more threads made these memory-bound kernels slower on the dev machine.
    ne.set_num_threads(min(8, performance.THREADS))
except ImportError:
    ne = None

THRESHOLD = np.float32(0.0031308)
SLOPE = np.float32(12.92)
GAIN = np.float32(1.055)
POWER = np.float32(1 / 2.4)
OFFSET = np.float32(0.055)


def srgb(rgb, contrast=0, nonnegative=False):
    if ne is None:
        return None
    x = rgb if nonnegative else np.maximum(rgb, 0)
    factor = np.float32(1 + (contrast or 0) / 125)
    half = np.float32(0.5)
    zero, one = np.float32(0), np.float32(1)
    values = dict(
        x=x,
        threshold=THRESHOLD,
        slope=SLOPE,
        gain=GAIN,
        power=POWER,
        offset=OFFSET,
        factor=factor,
        half=half,
        zero=zero,
        one=one,
    )
    expression = 'where(x <= threshold, x * slope, gain * x ** power - offset)'
    if contrast:
        expression = '(' + expression + ' - half) * factor + half'
    if contrast is not None:
        expression = 'where(v < zero, zero, where(v > one, one, v))'.replace(
            'v', '(' + expression + ')'
        )
    return ne.evaluate(expression, local_dict=values, optimization='moderate')


def tonal(image, a, cache=None, basis_key=None):
    if ne is None:
        return None
    image = np.asarray(image, dtype=np.float32)
    temp, tint = a['temperature'] / 100, a['tint'] / 100
    gains = np.asarray(
        [2 ** (0.4 * temp + 0.15 * tint), 2 ** (-0.15 * tint), 2 ** (-0.4 * temp + 0.15 * tint)],
        np.float32,
    )
    gain = 2 ** a['exposure'] * gains
    zero = np.float32(0)

    def balanced():
        return ne.evaluate(
            'image * gain', local_dict=dict(image=image, gain=gain), optimization='moderate'
        )

    x = balanced() if cache is None else cache.get('gain', basis_key, balanced)
    if any(a[k] for k in ('shadows', 'highlights', 'blacks', 'whites')):

        def weights():
            r, g, b = x[..., 0], x[..., 1], x[..., 2]
            cr, cg, cb = np.float32(0.2126), np.float32(0.7152), np.float32(0.0722)
            lum = ne.evaluate('r * cr + g * cg + b * cb', local_dict=locals())
            zero, one, power = np.float32(0), np.float32(1), np.float32(0.45)
            p = ne.evaluate(
                'where(lum < zero, zero, where(lum > one, one, lum)) ** power', local_dict=locals()
            )
            # These four exact float32 fields depend on linear gains, not on the
            # four range sliders. A coupled exposure-curve drag reuses them.
            values = dict(p=p, one=one)
            return np.stack(
                [
                    ne.evaluate(expr, local_dict=values, optimization='moderate')
                    for expr in ('(one-p)**2', 'p**3', '(one-p)**6', 'p**6')
                ]
            )

        fields = weights() if cache is None else cache.get('weights', basis_key, weights)
        result = native_kernels.range_srgb(x, fields, a)
        if result is not None:
            return result
        sw, hw, bw, ww = fields
        two = np.float32(2)
        sh, hi, bl, wh = [np.float32(a[k]) for k in ('shadows', 'highlights', 'blacks', 'whites')]
        d1, d2 = np.float32(65), np.float32(85)
        stops = ne.evaluate(
            '(sh * sw + hi * hw) / d1 + (bl * bw + wh * ww) / d2',
            local_dict=locals(),
            optimization='moderate',
        )[..., None]
        # stops has one value per pixel. Evaluate its exponential once before
        # broadcasting to RGB instead of repeating the power for each channel.
        stop_gain = ne.evaluate('two ** stops', local_dict=locals(), optimization='moderate')
        x = ne.evaluate('x * stop_gain', local_dict=locals(), optimization='moderate')
    return srgb(x, a['contrast'])


def rgb8(rgb):
    if ne is None:
        return np.clip(rgb * 255, 0, 255).astype(np.uint8)
    factor, low, high = np.float32(255), np.float32(0), np.float32(255)
    return ne.evaluate(
        'where(rgb < low, low, where(rgb > 1, high, rgb * factor))',
        local_dict=dict(rgb=rgb, factor=factor, low=low, high=high),
    ).astype(np.uint8)


def linear(rgb):
    if ne is None:
        return None
    threshold, slope = np.float32(0.04045), np.float32(12.92)
    gain, offset, power, zero = np.float32(1.055), np.float32(0.055), np.float32(2.4), np.float32(0)
    return ne.evaluate(
        'where(rgb <= threshold, rgb / slope, '
        'where((rgb + offset) / gain > zero, (rgb + offset) / gain, zero) ** power)',
        local_dict=locals(),
        optimization='moderate',
    )


def dehaze(rgb, air, transmission):
    if ne is None:
        return None
    floor_t = np.float32(0.22)
    t = transmission[..., None]
    x = ne.evaluate('(rgb - air) / where(t > floor_t, t, floor_t) + air', local_dict=locals())
    np.clip(x, 0, 1, out=x)
    return x


def blend(region, local, weight):
    if ne is None:
        region[:] = region * (1 - weight) + local * weight
    else:
        one = np.float32(1)
        ne.evaluate('region * (one - weight) + local * weight', local_dict=locals(), out=region)


def interp(rgb, values, index):
    if ne is None:
        return None
    lo, hi = values[index], values[index + 1]
    scale = np.float64(len(values) - 1)
    return ne.evaluate(
        'lo + (hi-lo) * (rgb * scale - index)', local_dict=locals(), optimization='moderate'
    ).astype(np.float32)


def grade(rgb, grading):
    if ne is None:
        return None
    import colorsys

    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    cr, cg, cb = np.float32(0.2126), np.float32(0.7152), np.float32(0.0722)
    lum = ne.evaluate('r * cr + g * cg + b * cb', local_dict=locals())
    balance = np.float32(grading.get('balance', 0) / 300)
    zero, one, two = np.float32(0), np.float32(1), np.float32(2)
    tone = ne.evaluate(
        'where(lum + balance < zero, zero, where(lum + balance > one, one, lum + balance))',
        local_dict=locals(),
    )
    base, amount, pi = np.float32(0.2), np.float32(0.8), np.float32(np.pi)
    protection = ne.evaluate(
        'base + amount * sin(pi * where(lum < zero, zero, where(lum > one, one, lum)))',
        local_dict=locals(),
    )[..., None]
    result = rgb.copy()
    for zone, expression in [
        ('shadows', '(one-tone)**2'),
        ('midtones', 'two*tone*(one-tone)'),
        ('highlights', 'tone**2'),
    ]:
        hue, strength = grading.get(zone, [0, 0])
        if not strength:
            continue
        weight = ne.evaluate(expression, local_dict=locals(), optimization='moderate')[..., None]
        color = np.asarray(colorsys.hsv_to_rgb((hue % 360) / 360, 1, 1), np.float32)
        color -= np.dot(color, [0.2126, 0.7152, 0.0722])
        strength, divisor = np.float32(strength), np.float32(350)
        ne.evaluate(
            'result + (weight * protection * strength / divisor) * color',
            local_dict=locals(),
            out=result,
        )
    np.clip(result, 0, 1, out=result)
    return result


def vignette(rgb, edge, amount):
    if ne is None:
        return None
    edge = edge[..., None]
    strength = np.float64(amount)
    divisor = np.float64(55 if amount < 0 else 170)
    if amount < 0:
        two = np.float64(2)
        gain = ne.evaluate(
            'two ** (edge * strength / divisor)', local_dict=locals(), optimization='moderate'
        )
        return ne.evaluate('rgb * gain', local_dict=locals()).astype(np.float32)
    one = np.float32(1)
    return ne.evaluate('rgb + (one-rgb) * (edge * strength / divisor)', local_dict=locals()).astype(
        np.float32
    )
