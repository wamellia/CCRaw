import shutil
import threading

import numpy as np
from PIL import Image

from ccraw.photo_agent.store import Project
from ccraw.photo_agent.analysis import index_project, suggestions, cluster_faces


def album(tmp_path):
    project = Project.create(tmp_path / 'album.ccrawagent', '相册')
    rng = np.random.default_rng(8)
    image = rng.integers(0, 255, (180, 240, 3), dtype=np.uint8)
    path = tmp_path / 'first.png'
    Image.fromarray(image).save(path)
    copy = tmp_path / 'copy.png'
    shutil.copyfile(path, copy)
    dark = tmp_path / 'dark.png'
    Image.new('RGB', (240, 180), (0, 0, 0)).save(dark)
    corrupt = tmp_path / 'broken.jpg'
    corrupt.write_bytes(b'not an image')
    project.import_paths([path, copy, dark, corrupt])
    return project, path


def test_scan_real_files_is_incremental_and_isolates_corrupt_photos(tmp_path):
    project, path = album(tmp_path)
    result = index_project(project)
    assert result['analyzed'] == 3 and result['failed'] == 1
    report = suggestions(project)
    assert len(report['duplicates']) == 1
    assert len(report['duplicates'][0]['photos']) == 2
    assert any('曝光偏低' in item['reasons'] for item in report['quality'])
    assert index_project(project)['analyzed'] == 0
    Image.new('RGB', (250, 180), 'white').save(path)
    assert index_project(project)['analyzed'] == 1


def test_modified_time_is_not_asserted_to_be_capture_time(tmp_path):
    project, _ = album(tmp_path)
    index_project(project)
    for photo in project.photos():
        if photo['status'] == 'ready':
            assert photo['facts']['captured_at'] is None
            assert photo['facts']['time_source'] == 'file_modified'
            assert photo['facts']['person_count'] is None


def test_cancel_does_not_consume_remaining_photos_or_modify_original(tmp_path):
    project, path = album(tmp_path)
    before = path.read_bytes()
    cancel = threading.Event()
    cancel.set()
    assert index_project(project, cancel=cancel)['cancelled']
    assert project.summary()['indexed'] == 0
    assert path.read_bytes() == before


def test_face_clusters_are_anonymous_conservative_and_stable():
    faces = [
        dict(id='a:0', photo_id='a', vector=[1, 0, 0]),
        dict(id='b:0', photo_id='b', vector=[0.99, 0.05, 0]),
        dict(id='c:0', photo_id='c', vector=[0, 1, 0]),
    ]
    groups = cluster_faces(faces)
    assert len(groups) == 2
    assert groups == cluster_faces(list(reversed(faces)))
    assert all(g['label'].startswith('人物簇') for g in groups)
    assert not any('name' in g for g in groups)


def test_scan_uses_upright_dimensions_and_exif_time_for_events(tmp_path):
    project = Project.create(tmp_path / 'exif.ccrawagent', '拍摄')
    for i in range(2):
        image = Image.new('RGB', (120, 80), (50, 80, 110))
        exif = Image.Exif()
        exif[274] = 6
        exif[36867] = f'2025:07:12 18:{i:02}:00'
        path = tmp_path / f'camera{i}.jpg'
        image.save(path, exif=exif)
        project.import_paths([path])
    index_project(project)
    for photo in project.photos():
        assert (photo['facts']['width'], photo['facts']['height']) == (80, 120)
        assert photo['facts']['captured_at'].startswith('2025-07-12')
    assert len(suggestions(project)['events'][0]['photos']) == 2
