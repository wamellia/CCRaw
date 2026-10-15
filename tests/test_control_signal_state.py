"""Editor refreshes must preserve signal guards owned by their caller."""

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QCheckBox, QListWidget

from ccraw.editing_tools import EditingToolsMixin
from ccraw.widgets import AdjustSlider


@pytest.mark.parametrize('control', ['slider', 'spin'])
def test_adjust_slider_refresh_preserves_outer_signal_guard(app, control):
    widget = AdjustSlider('曝光', -5, 5, 0.1)
    target = getattr(widget, control)
    target.blockSignals(True)
    values = []
    widget.changed.connect(values.append)

    widget.setValue(1.5)

    assert widget.slider.value() == 15
    assert widget.spin.value() == 1.5
    assert target.signalsBlocked()
    assert not values


@pytest.mark.parametrize('source, guarded', [('slider', 'spin'), ('spin', 'slider')])
def test_adjust_slider_user_change_preserves_other_control_guard(app, source, guarded):
    widget = AdjustSlider('曝光', -5, 5, 0.1)
    getattr(widget, guarded).blockSignals(True)
    values = []
    widget.changed.connect(values.append)

    getattr(widget, source).setValue(15 if source == 'slider' else 1.5)

    assert widget.slider.value() == 15
    assert widget.spin.value() == 1.5
    assert values == [1.5]
    assert getattr(widget, guarded).signalsBlocked()


def test_adjust_slider_programmatic_refresh_does_not_disable_user_changes(app):
    widget = AdjustSlider('曝光', -5, 5, 0.1)
    values = []
    widget.changed.connect(values.append)
    widget.setValue(1.5)
    widget.slider.setValue(20)
    widget.spin.setValue(-0.5)
    assert values == [2.0, -0.5]
    assert widget.slider.value() == -5


def test_retouch_refresh_keeps_outer_list_and_checkbox_guards(app):
    state = SimpleNamespace(
        edits={'retouch': [{'kind': 'heal', 'enabled': True}]},
        retouch_list=QListWidget(),
        retouch_enabled=QCheckBox(),
    )
    state.select_retouch = lambda: EditingToolsMixin.select_retouch(state)
    state.retouch_list.blockSignals(True)
    state.retouch_enabled.blockSignals(True)

    EditingToolsMixin.refresh_retouch(state, 0)

    assert state.retouch_list.count() == 1
    assert state.retouch_list.currentRow() == 0
    assert state.retouch_enabled.isChecked()
    assert state.retouch_list.signalsBlocked()
    assert state.retouch_enabled.signalsBlocked()
