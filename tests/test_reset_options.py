"""Both reset scopes are reachable from the editor, without changing originals."""

import copy
import hashlib
from pathlib import Path

from ccraw.scheduler import Activity
from test_ui import wait_until


def test_sidebar_reset_options_keep_whole_photo_reset_when_section_collapsed(window):
    w = window
    original = hashlib.sha256(Path(w.source_path).read_bytes()).hexdigest()
    w.controls['exposure'].spin.setValue(1)
    w.controls['saturation'].spin.setValue(30)
    w.commit()
    before = copy.deepcopy(w.edits)
    w.select_editor_section('color')
    w.editor_groups.sections['color'].header.click()
    assert not w.editor_reset_button.isEnabled()
    assert w.editor_reset_more_button.isEnabled()
    assert w.menu_reset in w.editor_reset_more_button.menu().actions()
    assert not w.reset_current_action.isEnabled()
    w.menu_reset.trigger()
    assert w.edits['adjustments']['exposure'] == 0
    assert w.edits['adjustments']['saturation'] == 0
    w.undo(-1)
    assert w.edits == before
    assert hashlib.sha256(Path(w.source_path).read_bytes()).hexdigest() == original
    wait_until(lambda: not w.jobs and not w.timer.isActive())


def test_sidebar_reset_options_are_disabled_during_image_work(window):
    w = window
    w.edits['adjustments']['exposure'] = 1.0
    before = copy.deepcopy(w.edits)
    w.work.begin(Activity.AI)
    try:
        w.refresh_access()
        assert not w.editor_reset_more_button.isEnabled()
        assert not w.menu_reset.isEnabled()
        assert not w.reset_current_action.isEnabled()
        w.reset_edits()
        assert w.edits == before
    finally:
        w.work.end(Activity.AI)
        w.refresh_access()
