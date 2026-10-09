"""Incremental local ingestion and non-destructive organization suggestions."""

from datetime import datetime
import hashlib
from pathlib import Path
import threading

import cv2
import numpy as np
from PIL import Image, ImageOps

from .models import LocalModels, digest, normalized
from .store import identifier

ANALYSIS_VERSION = 'photo-analysis-v1'


def preview_image(path):
    from .. import engine

    try:
        with Image.open(path) as source:
            if source.width * source.height > 400_000_000:
                raise ValueError('照片像素超出范围。')
            exif = source.getexif()
            size = source.size
            if exif.get(274) in (5, 6, 7, 8):
                size = size[::-1]
            details = dict(exif.get_ifd(34665)) if 34665 in exif else {}
            captured = details.get(36867) or exif.get(36867) or exif.get(306)
            source.draft('RGB', (1536, 1536))
            image = ImageOps.exif_transpose(source)
            image.thumbnail((768, 768), Image.Resampling.LANCZOS)
            return image.convert('RGB'), size, captured
    except (OSError, ValueError):
        if Path(path).suffix.lower() not in engine.RAW_EXTENSIONS:
            raise
    rgb, info = engine.load_image(path, 768)
    rgb = engine.to_srgb(engine.develop.apply(rgb, info.get('develop', {})))
    return (
        Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)),
        (info['width'], info['height']),
        info.get('photo', {}).get('date'),
    )


def analyze_photo(photo, project, models, cancel):
    path = Path(photo['path'])
    before = path.stat()
    sha = digest(path, cancel)
    image, size, captured = preview_image(path)
    if cancel.is_set():
        raise InterruptedError('扫描已取消。')
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    small = cv2.resize(gray, (32, 32)).astype(np.float32)
    dct = cv2.dct(small)[:8, :8].reshape(-1)[1:]
    bits = dct > np.median(dct)
    phash = f'{sum(int(bit) << i for i, bit in enumerate(bits)):016x}'
    quality = dict(
        sharpness=float(cv2.Laplacian(gray, cv2.CV_32F).var()),
        dark=float(np.mean(gray < 12)),
        bright=float(np.mean(gray > 245)),
    )
    flags = []
    if quality['dark'] > 0.6:
        flags.append('曝光偏低')
    if quality['bright'] > 0.6:
        flags.append('高光偏多')
    if quality['sharpness'] < 25:
        flags.append('疑似模糊')
    captured_at = None
    if isinstance(captured, str):
        try:
            captured_at = datetime.strptime(
                captured[:19], '%Y-%m-%d %H:%M:%S' if captured[4] == '-' else '%Y:%m:%d %H:%M:%S'
            ).isoformat()
        except ValueError:
            pass
    features = models.analyze(image)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('扫描时原片发生变化，请重新扫描。')
    preview = project.directory('previews') / (photo['id'] + '.jpg')
    if preview.exists() and (
        preview.is_symlink() or preview.resolve().parent != project.directory('previews')
    ):
        raise ValueError('预览文件路径无效。')
    temporary = preview.with_name(identifier() + '.tmp')
    try:
        with temporary.open('xb') as stream:
            image.save(stream, format='JPEG', quality=88)
        project.directory('previews')
        temporary.replace(preview)
    finally:
        temporary.unlink(missing_ok=True)
    facts = dict(
        width=size[0],
        height=size[1],
        captured_at=captured_at,
        file_modified=datetime.fromtimestamp(after.st_mtime).isoformat(),
        time_source='exif' if captured_at else 'file_modified',
        quality=quality,
        flags=flags,
        phash=phash,
        mean_rgb=np.asarray(image).mean(axis=(0, 1)).tolist(),
        person_count=None,
        preview=preview.name,
        analysis_signature=ANALYSIS_VERSION + ':' + models.signature,
        **features,
    )
    return facts, sha, after


def index_project(project, cancel=None, progress=lambda *_: None, *, force=False, models=None):
    cancel = cancel or threading.Event()
    models = models or LocalModels()
    job_id = project.start_job('index')
    analyzed = failed = skipped = visited = 0
    count = project.summary()['photos']
    try:
        for offset in range(0, count, 200):
            for photo in project.photos(200, offset):
                if cancel.is_set():
                    raise InterruptedError('扫描已取消。')
                visited += 1
                signature = ANALYSIS_VERSION + ':' + models.signature
                try:
                    stat = Path(photo['path']).stat()
                    unchanged = (
                        stat.st_size == photo['size']
                        and stat.st_mtime_ns == photo['mtime']
                        and photo['facts'].get('analysis_signature') == signature
                    )
                    if unchanged and not force and photo['status'] in ('ready', 'failed'):
                        skipped += 1
                        continue
                    facts, sha, stat = analyze_photo(photo, project, models, cancel)
                    project.update_photo(
                        photo['id'], facts=facts, sha=sha, size=stat.st_size, mtime=stat.st_mtime_ns
                    )
                    analyzed += 1
                except InterruptedError:
                    raise
                except Exception as error:
                    from ..logs import redact

                    project.update_photo(
                        photo['id'],
                        facts={'error': redact(str(error))[:400], 'analysis_signature': signature},
                        status='failed',
                    )
                    failed += 1
                progress(round(visited / max(1, count) * 100), f'分析照片 {visited} / {count}')
        project.finish_job(job_id, 'completed')
        return dict(analyzed=analyzed, failed=failed, skipped=skipped, cancelled=False)
    except InterruptedError:
        project.finish_job(job_id, 'cancelled')
        return dict(analyzed=analyzed, failed=failed, skipped=skipped, cancelled=True)
    except BaseException:
        project.finish_job(job_id, 'failed')
        raise


def cluster_faces(faces):
    groups = []
    for face in sorted(faces, key=lambda f: f['id']):
        try:
            vector = normalized(face['vector'])
        except ValueError:
            continue
        group = next(
            (
                g
                for g in groups
                if len(g['vectors']) < 64
                and g['vectors'][0].shape == vector.shape
                and all(float(v @ vector) >= 0.55 for v in g['vectors'])
            ),
            None,
        )
        if group is None:
            group = dict(
                id='person-' + hashlib.sha256(face['id'].encode()).hexdigest()[:12],
                label=f'人物簇 {len(groups) + 1}',
                photos=[],
                faces=[],
                vectors=[],
            )
            groups.append(group)
        group['vectors'].append(vector)
        group['faces'].append(face['id'])
        if face['photo_id'] not in group['photos']:
            group['photos'].append(face['photo_id'])
    return [{k: v for k, v in g.items() if k != 'vectors'} for g in groups]


def suggestions(project):
    # Bound a suggestion pass; coverage is shown rather than hiding an incomplete scan.
    photos = [p for p in project.photos(10000) if p['status'] == 'ready']
    exact, similar, quality, events, faces = {}, [], [], [], []
    buckets = {}
    for photo in photos:
        facts, photo_id = photo['facts'], photo['id']
        if photo['sha']:
            exact.setdefault(photo['sha'], []).append(photo_id)
        if facts.get('flags'):
            quality.append(dict(photos=[photo_id], reasons=facts['flags'], confidence='heuristic'))
        phash = int(facts['phash'], 16)
        # Four locality buckets bound comparisons while retaining close hashes.
        candidates = {
            p['id']: p
            for shift in (0, 16, 32, 48)
            for p in buckets.get((shift, (phash >> shift) & 65535), [])
        }
        for candidate in list(candidates.values())[:128]:
            other = candidate['facts']
            if candidate['sha'] == photo['sha']:
                continue
            distance = (phash ^ int(other['phash'], 16)).bit_count()
            aspect = abs(facts['width'] / facts['height'] - other['width'] / other['height'])
            color = np.linalg.norm(np.array(facts['mean_rgb']) - other['mean_rgb'])
            if distance <= 6 and aspect < 0.08 and color < 35:
                similar.append(
                    dict(
                        photos=[candidate['id'], photo_id],
                        distance=distance,
                        confidence='candidate',
                    )
                )
                break
        for shift in (0, 16, 32, 48):
            bucket = buckets.setdefault((shift, (phash >> shift) & 65535), [])
            bucket.append(photo)
            if len(bucket) > 128:
                del bucket[0]
        for i, face in enumerate(facts.get('faces', [])):
            if len(faces) < 3000:
                faces.append(dict(id=f'{photo_id}:{i}', photo_id=photo_id, vector=face['vector']))
    timed = sorted(
        (p for p in photos if p['facts'].get('captured_at')),
        key=lambda p: p['facts']['captured_at'],
    )
    for photo in timed:
        captured = photo['facts']['captured_at']
        previous = events[-1] if events else None
        if (
            previous
            and (
                datetime.fromisoformat(captured) - datetime.fromisoformat(previous['end'])
            ).total_seconds()
            <= 4 * 3600
        ):
            previous['photos'].append(photo['id'])
            previous['end'] = captured
        else:
            events.append(
                dict(
                    label=f'事件 {len(events) + 1}',
                    start=captured,
                    end=captured,
                    photos=[photo['id']],
                    confidence='time_candidate',
                )
            )
    return dict(
        duplicates=[
            dict(photos=ids, confidence='sha256') for ids in exact.values() if len(ids) > 1
        ][:200],
        similar=similar[:200],
        quality=quality[:200],
        events=events[:200],
        people=cluster_faces(faces),
        coverage=dict(analyzed=len(photos), total=project.summary()['photos'], face_limit=3000),
        features=dict(
            semantic=any(p['facts'].get('features', {}).get('semantic') for p in photos),
            faces=any(p['facts'].get('features', {}).get('faces') for p in photos),
        ),
    )
