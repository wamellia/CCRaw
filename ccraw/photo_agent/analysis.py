"""Incremental local ingestion and non-destructive organization suggestions."""

from datetime import datetime
from zoneinfo import ZoneInfo
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
    # Unavailable models mean unknown / retained evidence, never "no faces".
    retained = []
    if not features.get('features', {}).get('semantic'):
        for key in ('vector', 'scenes'):
            if photo['facts'].get(key):
                features[key] = photo['facts'][key]
                retained.append(key)
    if not features.get('features', {}).get('faces') and photo['facts'].get('faces'):
        features['faces'] = photo['facts']['faces']
        retained.append('faces')
    if retained:
        features['retained_features'] = retained
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
    facts['format'] = path.suffix.lower().lstrip('.')
    try:
        with Image.open(path) as metadata_source:
            exif = metadata_source.getexif()
            details = dict(exif.get_ifd(34665)) if 34665 in exif else {}
            facts.update(
                camera=' '.join(str(exif.get(key, '')) for key in (271, 272)).strip(),
                lens=str(details.get(42036) or exif.get(42036) or ''),
                iso=int(details.get(34855) or exif.get(34855) or 0),
            )
    except (OSError, ValueError, TypeError):
        pass
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
                        facts={
                            **photo['facts'],
                            'error': redact(str(error))[:400],
                            'analysis_signature': signature,
                        },
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


def cluster_faces(faces, cancel=None):
    groups, buckets = [], {}
    for face in sorted(faces, key=lambda f: f['id']):
        if cancel and cancel.is_set():
            raise InterruptedError('人物聚类已取消。')
        try:
            vector = normalized(face['vector'])
        except ValueError:
            continue
        dominant = int(np.argmax(np.abs(vector)))
        key = (len(vector), dominant, bool(vector[dominant] >= 0))
        candidates = buckets.get(key, [])[-128:]
        group = next(
            (
                g
                for g in candidates
                if g['vectors'][0].shape == vector.shape
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
            buckets.setdefault(key, []).append(group)
        if len(group['vectors']) < 32:
            group['vectors'].append(vector)
        group['faces'].append(face['id'])
        if face['photo_id'] not in group['photos']:
            group['photos'].append(face['photo_id'])
    return [{k: v for k, v in g.items() if k != 'vectors'} for g in groups]


def suggestions(project, cancel=None, progress=lambda *_: None):
    cancel = cancel or threading.Event()
    from ..catalog import Catalog

    catalog = Catalog(project)
    with project.connect() as db:
        removed = {
            r[0]
            for r in db.execute('SELECT photo_ref FROM photo_state WHERE removed_at IS NOT NULL')
        }
    photos = []
    total = project.summary()['photos']
    for offset in range(0, total, 200):
        if cancel.is_set():
            raise InterruptedError('整理建议已取消；已完成的照片分析会保留。')
        photos.extend(
            p
            for p in project.photos(200, offset)
            if p['status'] == 'ready' and p['id'] not in removed
        )
        progress(
            round(min(total, offset + 200) / max(total, 1) * 30),
            f'读取分析事实 {min(total, offset + 200)} / {total}',
        )
    exact, similar, quality, events, faces = {}, [], [], [], []
    buckets = {}
    for number, photo in enumerate(photos):
        if cancel.is_set():
            raise InterruptedError('整理建议已取消；已完成的照片分析会保留。')
        if number % 200 == 0:
            project.preference(
                'analysis_checkpoint',
                {
                    'phase': 'similarity',
                    'processed': number,
                    'total': len(photos),
                    'version': ANALYSIS_VERSION,
                },
            )
            progress(
                30 + round(number / max(len(photos), 1) * 50),
                f'相似与质量候选 {number} / {len(photos)}',
            )
        facts, photo_id = photo['facts'], photo['id']
        if photo['sha']:
            exact.setdefault(photo['sha'], []).append(photo_id)
        if facts.get('flags'):
            quality.append(dict(photos=[photo_id], reasons=facts['flags'], confidence='heuristic'))
        phash = int(facts.get('phash', '0'), 16)
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
            aspect = abs(
                facts.get('width', 1) / max(facts.get('height', 1), 1)
                - other.get('width', 1) / max(other.get('height', 1), 1)
            )
            color = np.linalg.norm(
                np.array(facts.get('mean_rgb', [0, 0, 0])) - other.get('mean_rgb', [0, 0, 0])
            )
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
            faces.append(dict(id=f'{photo_id}:{i}', photo_id=photo_id, vector=face['vector']))
    timed = []
    timezone = ZoneInfo(project.manifest['timezone'])
    for photo in photos:
        try:
            captured = datetime.fromisoformat(photo['facts'].get('captured_at'))
            captured = captured if captured.tzinfo else captured.replace(tzinfo=timezone)
            timed.append((captured.timestamp(), photo))
        except (TypeError, ValueError):
            continue
    timed.sort(key=lambda pair: (pair[0], pair[1]['id']))
    burst = []
    previous_time = None
    for index, (when, photo) in enumerate(timed):
        if index % 200 == 0:
            if cancel.is_set():
                raise InterruptedError('事件与连拍建议已取消；已完成的分析保留。')
            project.preference(
                'analysis_checkpoint',
                {
                    'phase': 'events',
                    'processed': index,
                    'total': len(timed),
                    'version': ANALYSIS_VERSION,
                },
            )
            progress(
                80 + round(index / max(1, len(timed)) * 4), f'事件与连拍候选 {index}/{len(timed)}'
            )
        captured = photo['facts']['captured_at']
        previous = events[-1] if events else None
        if previous and previous_time is not None and when - previous_time <= 4 * 3600:
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
        previous_time = when
        same_scene = False
        if burst and when - burst[-1][0] <= 3:
            left, right = burst[-1][1]['facts'], photo['facts']
            if left.get('phash') and right.get('phash'):
                same_scene = (int(left['phash'], 16) ^ int(right['phash'], 16)).bit_count() <= 12
        if not same_scene:
            if len(burst) > 1:
                similar.append(
                    dict(
                        photos=[item[1]['id'] for item in burst],
                        grouping='burst',
                        start=burst[0][1]['facts']['captured_at'],
                        confidence='time_and_scene_candidate',
                    )
                )
            burst = []
        burst.append((when, photo))
    if len(burst) > 1:
        similar.append(
            dict(
                photos=[item[1]['id'] for item in burst],
                grouping='burst',
                start=burst[0][1]['facts']['captured_at'],
                confidence='time_and_scene_candidate',
            )
        )
    progress(85, f'匿名人物聚类 {len(faces)} 张脸')
    inferred_people = catalog.stabilize_clusters(cluster_faces(faces, cancel), cancel)
    people = catalog.apply_cluster_corrections(inferred_people)
    report = dict(
        duplicates=[
            dict(photos=ids, confidence='sha256') for ids in exact.values() if len(ids) > 1
        ],
        similar=similar,
        quality=quality,
        events=events,
        people=people,
        inferred_people=inferred_people,
        coverage=dict(analyzed=len(photos), total=total, faces=len(faces), candidate_limit=128),
        features=dict(
            semantic=any(p['facts'].get('features', {}).get('semantic') for p in photos),
            faces=any(p['facts'].get('features', {}).get('faces') for p in photos),
        ),
    )
    lookup = {p['id']: p for p in photos}
    with project.connect() as db:
        decisions = {
            row['id']: row['decision']
            for row in db.execute('SELECT id,decision FROM suggestion_decisions')
        }
    for kind in ('duplicates', 'similar', 'quality', 'events', 'people'):
        for group in report[kind]:
            group['decision'] = decisions.get(catalog.suggestion_id(kind, group))
            if kind in ('duplicates', 'similar'):

                def quality_rank(ref):
                    quality = lookup[ref]['facts'].get('quality', {})
                    clipping = min(
                        1,
                        max(0, float(quality.get('bright', 0)))
                        + max(0, float(quality.get('dark', 0))),
                    )
                    return (-float(quality.get('sharpness', 0)) * (1 - clipping), ref)

                ranked = sorted(
                    group['photos'],
                    key=quality_rank,
                )
                group['best_candidate'] = ranked[0] if ranked else None
                group['reasons'] = [
                    (
                        '3 秒内且场景相近的连拍候选'
                        if group.get('grouping') == 'burst'
                        else 'SHA256 相同'
                        if kind == 'duplicates'
                        else f'相似哈希距离 {group.get("distance", 0)}'
                    ),
                    '按清晰度与曝光剪切比例排序；建议需人工判断',
                ]
    project.preference(
        'analysis_checkpoint',
        {
            'phase': 'completed',
            'processed': len(photos),
            'total': len(photos),
            'version': ANALYSIS_VERSION,
        },
    )
    progress(100, '整理建议已完成')
    return report
