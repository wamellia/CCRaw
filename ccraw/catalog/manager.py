"""Classic editor bridge to the shared catalog, without a project creation dialog."""

import copy
import hashlib
from pathlib import Path


def CatalogWindow(project, parent=None):
    """Return the same fully functional catalog workspace used by Photo Agent."""
    from ..photo_agent.ui import AgentWindow
    from PySide6.QtCore import Qt

    window = AgentWindow(project)
    if parent is not None:
        window.setParent(parent, Qt.WindowType.Window)
    window.setWindowTitle(project.manifest['name'] + ' · 相册管理 · CCRaw')
    window.chat_panel.hide()
    window.chat_toggle.setText('展开对话')
    return window


def open_catalog_for_editor(editor):
    """Synchronize editor documents into a retained, automatically created catalog.

    A saved album maps to one persistent per-user catalog. An unsaved editing
    session retains one catalog on its editor. Catalog editing keeps source bytes
    intact; recipes/snapshots are shared through Project preferences.
    """
    from .. import host, model
    from ..photo_agent.store import Project

    editor.stash_document()
    documents = editor.documents
    if not documents:
        editor.statusBar().showMessage('请先导入照片。')
        return None
    project = getattr(editor, 'catalog_project', None)
    if project is None:
        album = getattr(editor, 'album_path', '')
        key = str(Path(album).resolve()) if album else '|'.join(sorted(documents))
        name = Path(album).stem if album else '快捷编辑相册'
        path = (
            host.data_folder()
            / 'catalogs'
            / (hashlib.sha256(key.encode()).hexdigest()[:24] + '.ccrawagent')
        )
        project = Project.open(path) if path.is_file() else Project.create(path, name)
        editor.catalog_project = project
    project.import_paths(documents)
    from .repository import Catalog

    catalog = Catalog(project)
    with project.connect() as db:
        refs = {r['path']: r['id'] for r in db.execute('SELECT id,path FROM photos')}
        overrides = {}
        for row in db.execute('SELECT photo_ref,field FROM metadata_overrides'):
            overrides.setdefault(row['photo_ref'], set()).add(row['field'])
    preferences = project.preferences()
    for path, document in documents.items():
        ref = refs.get(str(Path(path).resolve()))
        if ref and document['initialized']:
            recipe = model.validate(document['edits'])
            if preferences.get('recipe:' + ref) != recipe:
                project.preference('recipe:' + ref, recipe)
            project.preference('snapshots:' + ref, model.validate_snapshots(document['snapshots']))
            metadata = document.get('metadata', {})
            defaults = {
                key: metadata[source]
                for key, source in [('author', 'creator'), ('copyright', 'copyright')]
                if metadata.get(source)
            }
            current = catalog.metadata(ref)
            defaults = {
                key: value
                for key, value in defaults.items()
                if not current.get(key) and key not in overrides.get(ref, set())
            }
            if defaults:
                catalog.edit_metadata([ref], defaults)
            if (
                metadata.get('keywords')
                and not current['keywords']
                and '_keywords' not in overrides.get(ref, set())
            ):
                catalog.edit_keywords([ref], metadata['keywords'], mode='append')
    window = getattr(editor, 'catalog_window', None)
    if window is None:
        window = CatalogWindow(project, editor)
        editor.catalog_window = window
        # Copies are edited from the manager with their own photo_ref. Physical
        # documents in this classic window receive committed catalog recipes.
        original_close = window.closeEvent

        def close(event):
            original_close(event)
            if event.isAccepted():
                with project.connect() as db:
                    current_paths = {
                        r['id']: r['path'] for r in db.execute('SELECT id,path FROM photos')
                    }
                paths = {
                    path: current_paths[ref]
                    for path, ref in window.classic_refs.items()
                    if ref in current_paths
                }
                if hasattr(editor, 'relink_catalog_documents'):
                    try:
                        changes = editor.relink_catalog_documents(paths)
                    except ValueError as exc:
                        editor.statusBar().showMessage(str(exc))
                        changes = {}
                    window.classic_refs = {
                        changes.get(path, path): ref for path, ref in window.classic_refs.items()
                    }
                preferences = project.preferences()
                for path, document in editor.documents.items():
                    ref = window.classic_refs.get(str(Path(path).resolve()))
                    recipe = preferences.get('recipe:' + str(ref))
                    if recipe and ref:
                        document['edits'] = model.validate(recipe)
                        document['snapshots'] = model.validate_snapshots(
                            preferences.get('snapshots:' + ref, [])
                        )
                        document['history'] = model.History(document['edits'])
                        metadata = catalog.metadata(ref)
                        document['metadata'] = {
                            **document.get('metadata', {}),
                            **{
                                key: metadata[key]
                                for key in (
                                    'title',
                                    'description',
                                    'copyright',
                                    'location',
                                    'keywords',
                                    'person_count',
                                )
                            },
                            'creator': metadata['author'],
                        }
                if getattr(editor, 'source_path', '') in editor.documents and hasattr(
                    editor, 'open_path'
                ):
                    document = editor.documents[editor.source_path]
                    editor.edits = copy.deepcopy(document['edits'])
                    editor.snapshots = copy.deepcopy(document['snapshots'])
                    editor.history = copy.deepcopy(document['history'])
                    editor.open_path(editor.source_path)

        window.closeEvent = close
    window.classic_refs = refs
    window.refresh_catalog_navigation()
    window.refresh_gallery()
    window.show()
    window.raise_()
    return window


def install_catalog(editor):
    """Install one visible menu entry; LibraryMixin may call the bridge directly."""
    from PySide6.QtGui import QAction

    existing = getattr(editor, 'catalog_action_entry', None)
    if existing is not None:
        return existing
    action = QAction('管理相册', editor)
    action.triggered.connect(lambda: open_catalog_for_editor(editor))
    editor.menuBar().addAction(action)
    editor.catalog_action_entry = action
    return action
