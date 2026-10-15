"""Bounded XMP sidecars preserving properties outside the explicitly edited set.

Unknown namespace URIs, structured properties, attributes and comments survive
semantically; whitespace, prefix spelling and xpacket padding may change.
Metadata is portable. CCRaw recipe JSON is proprietary, and existing Adobe CRS
settings remain untouched unless an explicitly supported field is requested.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


NS = {
    'x': 'adobe:ns:meta/',
    'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'xmp': 'http://ns.adobe.com/xap/1.0/',
    'exif': 'http://ns.adobe.com/exif/1.0/',
    'tiff': 'http://ns.adobe.com/tiff/1.0/',
    'aux': 'http://ns.adobe.com/exif/1.0/aux/',
    'iptc': 'http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/',
    'crs': 'http://ns.adobe.com/camera-raw-settings/1.0/',
    'ccraw': 'https://ccraw.org/ns/recipe/1.0/',
}
for _prefix, _uri in NS.items():
    ET.register_namespace(_prefix, _uri)

MAX_BYTES = 4 * 1024 * 1024
MAX_ELEMENTS = 30000
MAX_DEPTH = 64
FIELDS = {
    'title': ('dc', 'title', 'Alt'),
    'caption': ('dc', 'description', 'Alt'),
    'creator': ('dc', 'creator', 'Seq'),
    'copyright': ('dc', 'rights', 'Alt'),
    'keywords': ('dc', 'subject', 'Bag'),
    'location': ('iptc', 'Location', None),
    'rating': ('xmp', 'Rating', None),
    'label': ('xmp', 'Label', None),
    'flag': ('crs', 'Pick', None),
    'date': ('exif', 'DateTimeOriginal', None),
    'make': ('tiff', 'Make', None),
    'camera_model': ('tiff', 'Model', None),
    'lens_model': ('aux', 'Lens', None),
    'iso': ('exif', 'ISOSpeedRatings', 'Seq'),
    'exposure_time': ('exif', 'ExposureTime', None),
    'f_number': ('exif', 'FNumber', None),
    'focal_length': ('exif', 'FocalLength', None),
}
CRS_FIELDS = {
    'Exposure2012': (-5, 5),
    'Temperature': (2000, 50000),
    'Tint': (-150, 150),
    'CropTop': (0, 1),
    'CropLeft': (0, 1),
    'CropBottom': (0, 1),
    'CropRight': (0, 1),
    'CropAngle': (-45, 45),
}


def _tag(prefix, name):
    return '{' + NS[prefix] + '}' + name


def _parse(packet):
    if not isinstance(packet, (str, bytes)):
        raise ValueError('XMP must be UTF XML text or bytes')
    if len(packet) > MAX_BYTES:
        raise ValueError('XMP exceeds 4 MiB bound')
    if isinstance(packet, bytes):
        try:
            if packet.startswith((b'\xff\xfe', b'\xfe\xff')):
                packet = packet.decode('utf-16')
            else:
                packet = packet.decode('utf-8-sig')
        except UnicodeError as exc:
            raise ValueError('XMP must be UTF-8 or BOM-marked UTF-16') from exc
    if len(packet.encode('utf-8')) > MAX_BYTES:
        raise ValueError('XMP exceeds 4 MiB bound')
    if re.search(r'<!\s*(DOCTYPE|ENTITY)\b', packet, re.I) or '\x00' in packet:
        raise ValueError('XMP DTD and entities are disabled')
    # Pull events are consumed per chunk so depth/node checks precede large trees.
    parser = ET.XMLPullParser(
        events=('start', 'end'),
        _parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True)),
    )
    root = None
    depth = count = 0
    try:
        for offset in range(0, len(packet), 4096):
            parser.feed(packet[offset : offset + 4096])
            for event, element in parser.read_events():
                if event == 'start':
                    root = root if root is not None else element
                    depth += 1
                    count += 1 + len(element.attrib)
                    if depth > MAX_DEPTH or count > MAX_ELEMENTS:
                        raise ValueError('XMP complexity exceeds bound')
                else:
                    depth -= 1
        parser.close()
    except ET.ParseError as exc:
        raise ValueError('invalid XMP XML') from exc
    if (
        root is None
        or root.find('.//' + _tag('rdf', 'RDF')) is None
        and root.tag != _tag('rdf', 'RDF')
    ):
        raise ValueError('XMP must contain an RDF packet')
    return root


def _descriptions(root):
    rdf = root if root.tag == _tag('rdf', 'RDF') else root.find('.//' + _tag('rdf', 'RDF'))
    return rdf, list(rdf.findall(_tag('rdf', 'Description')))


def _property(descs, tag):
    for desc in descs:
        if tag in desc.attrib:
            return [desc.attrib[tag]]
        element = desc.find(tag)
        if element is not None:
            values = element.findall('.//' + _tag('rdf', 'li'))
            if values:
                default = [
                    v
                    for v in values
                    if v.get('{http://www.w3.org/XML/1998/namespace}lang') == 'x-default'
                ]
                return [(v.text or '') for v in default or values]
            if any(element.find(_tag('rdf', kind)) is not None for kind in ('Bag', 'Seq', 'Alt')):
                return []
            return [element.text or '']
    return None


def _json_recipe(value):
    if not isinstance(value, dict):
        raise ValueError('CCRaw recipe must be an object')

    def bound(obj, depth=0):
        if depth > 24:
            raise ValueError('CCRaw recipe nesting exceeds bound')
        if isinstance(obj, dict):
            if len(obj) > 4096 or any(not isinstance(k, str) for k in obj):
                raise ValueError('invalid recipe object')
            for item in obj.values():
                bound(item, depth + 1)
        elif isinstance(obj, list):
            if len(obj) > 65536:
                raise ValueError('CCRaw recipe array exceeds bound')
            for item in obj:
                bound(item, depth + 1)

    bound(value)
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError('invalid CCRaw recipe JSON') from exc
    if len(text.encode('utf-8')) > 1024 * 1024:
        raise ValueError('CCRaw recipe exceeds 1 MiB bound')
    return text


def loads(packet):
    root = _parse(packet)
    _, descs = _descriptions(root)
    metadata = {}
    for key, (prefix, name, container) in FIELDS.items():
        values = _property(descs, _tag(prefix, name))
        if values is None:
            continue
        if key == 'rating':
            try:
                rating = int(values[0])
            except (ValueError, IndexError) as exc:
                raise ValueError('invalid XMP rating') from exc
            if not -1 <= rating <= 5:
                raise ValueError('invalid XMP rating')
            metadata[key] = rating
        elif key == 'flag':
            if values[0] not in ('-1', '0', '1'):
                raise ValueError('invalid CRS pick flag')
            metadata[key] = {'-1': 'reject', '0': 'none', '1': 'pick'}[values[0]]
        elif key == 'iso':
            metadata[key] = values[0] if values else ''
        else:
            metadata[key] = values if container in ('Bag', 'Seq') else (values[0] if values else '')
    recipe = None
    values = _property(descs, _tag('ccraw', 'Recipe'))
    if values:
        try:
            recipe = json.loads(values[0])
        except (ValueError, RecursionError) as exc:
            raise ValueError('invalid CCRaw recipe JSON') from exc
        _json_recipe(recipe)
    crs = {}
    for key in CRS_FIELDS:
        values = _property(descs, _tag('crs', key))
        if values:
            crs[key] = values[0]
    return {'metadata': metadata, 'recipe': recipe, 'crs': crs}


def read(path):
    with Path(path).open('rb') as file:
        return loads(file.read(MAX_BYTES + 1))


def _replace(descs, destination, tag, text, container=None):
    preserved_alt = []
    for desc in descs:
        desc.attrib.pop(tag, None)
        for child in list(desc):
            if child.tag == tag:
                if container == 'Alt':
                    preserved_alt.extend(
                        deepcopy(li)
                        for li in child.findall('.//' + _tag('rdf', 'li'))
                        if li.get('{http://www.w3.org/XML/1998/namespace}lang')
                        not in (None, 'x-default')
                    )
                desc.remove(child)
    if text is None:
        return
    node = ET.SubElement(destination, tag)
    if container:
        values = text if isinstance(text, list) else [text]
        sequence = ET.SubElement(node, _tag('rdf', container))
        for value in values:
            item = ET.SubElement(sequence, _tag('rdf', 'li'))
            if container == 'Alt':
                item.set('{http://www.w3.org/XML/1998/namespace}lang', 'x-default')
            item.text = value
        sequence.extend(preserved_alt)
    else:
        node.text = text


def merge(existing=None, metadata=None, *, recipe=None, crs=None):
    if existing is None:
        root = ET.Element(_tag('x', 'xmpmeta'))
        rdf = ET.SubElement(root, _tag('rdf', 'RDF'))
        descs = []
    else:
        root = _parse(existing)
        rdf, descs = _descriptions(root)
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError('XMP metadata must be an object')
    metadata = metadata or {}
    if set(metadata) - set(FIELDS):
        raise ValueError('unknown portable XMP field')
    if not descs:
        descs.append(ET.SubElement(rdf, _tag('rdf', 'Description'), {_tag('rdf', 'about'): ''}))
    destination = descs[0]
    for key, value in metadata.items():
        prefix, name, container = FIELDS[key]
        if value is not None:
            if key == 'rating':
                if isinstance(value, bool) or not isinstance(value, int) or not -1 <= value <= 5:
                    raise ValueError('XMP rating must be -1 to 5')
                value = str(value)
            elif key == 'flag':
                if value not in ('pick', 'reject', 'none'):
                    raise ValueError('XMP flag must be pick, reject or none')
                value = {'pick': '1', 'reject': '-1', 'none': '0'}[value]
            elif container in ('Bag', 'Seq'):
                if key == 'iso' and isinstance(value, str):
                    value = [value]
                if not isinstance(value, list) or len(value) > 4096:
                    raise ValueError('XMP keywords and creator must be bounded lists')
                if any(not isinstance(item, str) or len(item) > 4096 for item in value):
                    raise ValueError('XMP list values must be strings')
            elif not isinstance(value, str) or len(value) > 65536:
                raise ValueError('XMP text must be a bounded string')
        _replace(descs, destination, _tag(prefix, name), value, container)
    if recipe is not None:
        _replace(descs, destination, _tag('ccraw', 'Recipe'), _json_recipe(recipe))
    if crs is not None:
        if not isinstance(crs, dict) or set(crs) - set(CRS_FIELDS):
            raise ValueError('unsupported CRS field')
        import math

        for key, value in crs.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError('CRS values must be finite numbers')
            low, high = CRS_FIELDS[key]
            if not low <= value <= high:
                raise ValueError('CRS value outside supported bounds')
            _replace(descs, destination, _tag('crs', key), format(value, '.10g'))
    out = ET.tostring(root, encoding='unicode')
    loads(out)  # Never commit XML that cannot be safely read back.
    return out


def write_sidecar(original, metadata=None, *, recipe=None, crs=None, path=None):
    from ..export_presets import atomic_write

    original = Path(original)
    sidecar = Path(path) if path is not None else original.with_suffix('.xmp')
    if sidecar.suffix.lower() != '.xmp' or sidecar.resolve() == original.resolve():
        raise ValueError('XMP must be written to a separate .xmp sidecar')
    existing = None
    if sidecar.exists():
        with sidecar.open('rb') as file:
            existing = file.read(MAX_BYTES + 1)
    packet = merge(existing, metadata, recipe=recipe, crs=crs)
    return atomic_write(sidecar, lambda temp: temp.write_text(packet, encoding='utf-8'))
