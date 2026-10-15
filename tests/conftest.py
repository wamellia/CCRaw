"""Shared desktop fixtures, also available to independently selected test modules."""

from test_ui import app, window  # noqa: F401
import pytest


RUNTIME_TESTS = {
    'test_actual_new_models_and_noise_quality',
    'test_real_bundled_neural_model_changes_pixels_and_honors_cancel',
    'test_real_neural_tiled_output_has_no_join_seam',
    'test_neural_preview_dialog_works_offline',
    'test_remote_session_matches_in_process_inference',
    'test_native_crash_is_recorded_and_retried_on_the_next_provider',
    'test_session_routing',
    'test_spoken_instructions_are_recognized_offline',
    'test_real_person_and_background_networks_return_probabilities',
    'test_real_ffdnet_reduces_noise_and_has_no_tile_seams',
    'test_subject_is_background_inverse_and_depth_is_finite',
    'test_ai_denoise_adds_named_copy_with_metadata_and_neutral_recipe',
    'test_standalone_super_resolution_preview',
    'test_voice_instruction_is_recognized_and_sent',
}


def pytest_collection_modifyitems(items):
    # Explicit selection preserves failures when assets are required but missing.
    for item in items:
        if item.originalname in RUNTIME_TESTS:
            item.add_marker(pytest.mark.runtime_assets)


@pytest.fixture(autouse=True)
def release_closed_test_windows():
    """Closed windows from one test must not keep polling in following tests."""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QCoreApplication, QEvent

    instance = QApplication.instance()
    before = {id(w) for w in instance.topLevelWidgets()} if instance else set()
    yield
    instance = QApplication.instance()
    if instance is None:
        return
    for widget in instance.topLevelWidgets():
        if id(widget) in before or not widget.isHidden():
            continue
        if (
            getattr(widget, 'jobs', None)
            or getattr(widget, 'worker', None)
            or getattr(widget, '_model_busy', False)
        ):
            continue
        # WorkState flags alone also occur in synthetic test windows without
        # real jobs. Retaining those leaves a phantom global device blocker.
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
