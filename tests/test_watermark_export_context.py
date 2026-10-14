"""Template metadata travels to rendering without changing exported metadata."""

from ccraw import photo_metadata


def test_equivalent_focal_length_is_retained_separately_from_physical_length():
    metadata = photo_metadata.from_tags({'FocalLength': 23, 'FocalLengthIn35mmFormat': 35})
    assert metadata['focal'] == '23 mm'
    assert metadata['focal_equivalent'] == '35 mm'


def test_local_filename_and_folder_context_does_not_expose_a_file_path():
    photo = {'body': 'EOS R6'}
    context = photo_metadata.watermark_context(photo, 'C:/Private/Travel/IMG_001.nef')
    assert context == {'body': 'EOS R6', 'filename': 'IMG_001.nef', 'folder': 'Travel'}
    assert photo == {'body': 'EOS R6'}
