"""Navigation and grouped actions over the existing catalog repository."""

from pathlib import Path

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QSpinBox,
    QDialog,
    QDialogButtonBox,
)
from .. import i18n


def install_navigation(window):
    panel = QWidget()
    panel.setObjectName('catalogNavigation')
    panel.setMinimumWidth(145)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 8, 0)
    layout.addWidget(QLabel('图库导航'))
    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    tree.setMinimumWidth(130)
    tree.setAccessibleName('相册与筛选导航')
    tree.setTextElideMode(Qt.TextElideMode.ElideRight)
    window.catalog_navigation_tree = tree
    layout.addWidget(tree, 1)
    tree.currentItemChanged.connect(
        lambda item, _previous: (
            window.navigation.setCurrentIndex(
                window.navigation.findData(item.data(0, Qt.ItemDataRole.UserRole))
            )
            if item and item.data(0, Qt.ItemDataRole.UserRole)
            else None
        )
    )
    window.workspace_splitter.insertWidget(0, panel)
    window.workspace_splitter.setStretchFactor(0, 0)
    window.workspace_splitter.setStretchFactor(1, 4)
    window.workspace_splitter.setStretchFactor(2, 1)
    window.workspace_splitter.setSizes([175, 865, 320])
    window.catalog_navigation_panel = panel


def refresh_navigation(window):
    tree = window.catalog_navigation_tree
    blocker = QSignalBlocker(tree)
    expanded = {
        key
        for key, item in getattr(window, 'catalog_navigation_items', {}).items()
        if item.isExpanded()
    }
    tree.clear()
    window.catalog_navigation_items = {}
    library = QTreeWidgetItem(tree, ['照片'])
    albums = QTreeWidgetItem(tree, ['相册与文件夹'])
    filters = QTreeWidgetItem(tree, ['保存的筛选'])
    for category in (library, albums, filters):
        category.setFlags(category.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        category.setExpanded(True)
    records = window.catalog.albums()
    album_items = {}
    for index in range(window.navigation.count()):
        value = window.navigation.itemData(index)
        if value.startswith('album:'):
            continue
        parent = filters if value.startswith('preset:') else library
        item = QTreeWidgetItem(parent, [window.navigation.itemText(index)])
        item.setData(0, Qt.ItemDataRole.UserRole, value)
        if value.startswith('preset:'):
            item.setData(0, Qt.ItemDataRole.UserRole + 31, True)
        window.catalog_navigation_items[value] = item
    for album in records:
        prefix = {'folder': '▸ ', 'smart': '◇ ', 'album': ''}[album['kind']]
        item = QTreeWidgetItem([prefix + album['name']])
        value = 'album:' + album['id']
        item.setData(0, Qt.ItemDataRole.UserRole, value)
        item.setData(0, Qt.ItemDataRole.UserRole + 31, True)
        item.setToolTip(0, album['name'])
        album_items[album['id']] = item
        window.catalog_navigation_items[value] = item
    for album in records:
        parent = album_items.get(album['parent'], albums)
        parent.addChild(album_items[album['id']])
        album_items[album['id']].setExpanded('album:' + album['id'] in expanded)
    current = window.catalog_navigation_items.get(window.navigation.currentData())
    if current:
        tree.setCurrentItem(current)
        parent = current.parent()
        while parent:
            parent.setExpanded(True)
            parent = parent.parent()
    blocker.unblock()
    i18n.localize_window(window.catalog_navigation_tree, i18n.language())
    from ..ui import theme

    theme.style_hierarchy(window.catalog_navigation_tree)


def proxy_size_dialog(window):
    dialog = QDialog(window)
    dialog.setWindowTitle('离线代理尺寸')
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel('代理最长边；原片保持原始尺寸。'))
    edge = QSpinBox()
    edge.setRange(64, 8192)
    edge.setValue(window.proxy_edge.value())
    edge.setSuffix(' px')
    layout.addWidget(edge)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        window.proxy_edge.setValue(edge.value())


def install_management(window):
    window.catalog_menu = QMenu(window)
    window.catalog_actions = {}
    window.catalog_menus = {}
    groups = [
        (
            'albums',
            '相册与筛选',
            [
                ('new_album', '新建相册', window.new_album),
                ('new_smart', '新建智能相册', lambda: window.new_album('smart')),
                ('new_folder', '新建相册文件夹', lambda: window.new_album('folder')),
                ('save_filter', '保存筛选预设', window.save_filter_preset),
                ('edit_album', '相册名称 / 父级 / 规则', window.edit_album_dialog),
                ('cover', '相册封面', window.album_cover),
                ('order', '自定义相册顺序', window.album_order),
            ],
        ),
        (
            'selection',
            '选中照片',
            [
                ('compare', 'Compare 对比', window.compare_selected),
                ('survey', 'Survey 候选', window.survey_selected),
                ('add_album', '添加到相册', window.add_to_album),
                ('remove_album', '从当前相册移除', window.remove_from_album),
                ('stack', '建立堆叠', window.stack_selected),
                ('stack_ops', '堆叠操作', window.stack_dialog),
                ('auto_stack', '自动堆叠', window.auto_stack_dialog),
                ('copy', '建立虚拟副本', window.copy_selected),
                ('people', '人物簇校正', window.cluster_dialog),
            ],
        ),
        (
            'files',
            '原片、代理与回收站',
            [
                ('relink', '重关联原片', window.relink_selected),
                ('search_relink', '检索重关联目录', window.search_relink_folder),
                ('prefix', '替换目录前缀', window.relink_prefix_dialog),
                (
                    'missing',
                    '检查缺失原片',
                    lambda: window.catalog_action(window.catalog.refresh_missing),
                ),
                ('proxy_ops', '代理操作', window.proxy_dialog),
                ('proxy', '创建 16 位离线代理', window.proxy_selected),
                ('trash', '移入工程回收站', lambda: window.catalog_apply_state(removed=True)),
                ('restore', '恢复选中引用', lambda: window.catalog_apply_state(removed=False)),
                ('purge', '备份并清空选中回收记录', window.purge_selected),
            ],
        ),
        (
            'history',
            '历史与备份',
            [
                ('undo', '撤销管理动作', lambda: window.catalog_action(window.catalog.undo)),
                ('redo', '重做管理动作', lambda: window.catalog_action(window.catalog.redo)),
                ('backup', '数据库备份', window.backup_catalog),
            ],
        ),
    ]
    for key, title, entries in groups:
        menu = window.catalog_menu.addMenu(title)
        window.catalog_menus[key] = menu
        for action_key, label, callback in entries:
            action = QAction(label, window)
            # Resolve no-argument callbacks without leaking QAction's checked bool.
            action.triggered.connect(
                lambda _checked=False, cb=callback, key=action_key: (
                    cb() if window.catalog_operation_available(key) else None
                )
            )
            action.setObjectName('catalog_' + action_key)
            menu.addAction(action)
            window.catalog_actions[action_key] = action
        menu.aboutToShow.connect(window.update_catalog_controls)
    window.catalog_menu.aboutToShow.connect(window.update_catalog_controls)
    window.catalog_management_button = QPushButton('图库管理')
    window.catalog_management_button.setMenu(window.catalog_menu)
    window.catalog_management_button.setMinimumHeight(36)
    window.agent_top_layout.insertWidget(2, window.catalog_management_button)
    window.selection_more_button = QPushButton('更多操作')
    window.selection_more_button.setMinimumHeight(36)
    window.selection_more_button.setMenu(window.catalog_menus['selection'])
    window.photo_action_layout.addWidget(window.selection_more_button)
    window.proxy_button = window.catalog_actions['proxy']
    window.trash_button = window.catalog_actions['trash']
    window.undo_catalog_button = window.catalog_actions['undo']
    window.proxy_edge = QSpinBox(window)
    window.proxy_edge.hide()
    window.proxy_edge.setRange(64, 8192)
    window.proxy_edge.setValue(window.project.preferences().get('catalog.proxy_edge', 2560))
    window.proxy_edge.valueChanged.connect(
        lambda value: window.project.preference('catalog.proxy_edge', value)
    )
    window.proxy_size_action = window.catalog_menus['files'].addAction('离线代理尺寸…')
    window.proxy_size_action.triggered.connect(lambda: proxy_size_dialog(window))
    window.catalog_status = QLabel(window)
    window.catalog_status.hide()
    window.gallery.itemSelectionChanged.connect(window.update_catalog_controls)
    window.editor_button.setToolTip('选择一张有原片或可用离线代理的照片。')
    window.annotation_button.setToolTip('选择一张照片标注人数与标签。')
    window.recommend_button.setToolTip('选择 1–30 张已扫描且原片可用的照片。')
    window.update_catalog_controls()


def original_available(photo, *, scanned=False, live=True):
    """Cheap source checks; decoding and hashing stay at the operation boundary."""
    if scanned and not photo.get('sha'):
        return False
    if not live:
        return photo.get('status') != 'missing'
    try:
        source = Path(photo['path'])
        if not source.is_file():
            return False
        if scanned:
            stat = source.stat()
            return (stat.st_size, stat.st_mtime_ns) == (photo['size'], photo['mtime'])
        return True
    except OSError:
        return False


def editor_available(window, ref, *, allow_proxy=False):
    try:
        photo = window.project.photo(ref)
        if original_available(photo):
            return True
        policy = (
            window.project.preferences()
            .get('ccraw.settings', {})
            .get('project', {})
            .get('proxy_policy', 'auto')
        )
        if allow_proxy or policy in ('auto', 'proxy'):
            # A missing original only needs the managed proxy path, not a hash
            # of a potentially large or remote original on every selection.
            proxy = window.catalog.proxy(ref)
            return bool(proxy and Path(proxy['path']).is_file())
    except (ValueError, OSError):
        pass
    return False


def action_conditions(window):
    """Share prerequisites between presentation and direct callback guards."""
    refs = window.selected_ids()
    count = len(refs)
    albums = window.catalog.albums()
    album = next((a for a in albums if a['id'] == window.catalog_album), None)
    with window.project.connect() as db:
        undo = db.execute('SELECT 1 FROM catalog_commands WHERE undone=0 LIMIT 1').fetchone()
        redo = db.execute('SELECT 1 FROM catalog_commands WHERE undone=1 LIMIT 1').fetchone()
        originals = db.execute('SELECT count(*) FROM photos').fetchone()[0]
        eligible_stacks = db.execute(
            'SELECT count(*) FROM catalog_photos v WHERE is_copy=0 AND removed_at IS NULL '
            "AND json_extract(v.facts,'$.captured_at') IS NOT NULL "
            'AND NOT EXISTS(SELECT 1 FROM stack_members sm WHERE sm.photo_ref=v.id)'
        ).fetchone()[0]
        members = (
            db.execute(
                'SELECT count(*) FROM album_members WHERE album_id=?', (window.catalog_album,)
            ).fetchone()[0]
            if album
            else 0
        )
        selected = (
            [
                dict(row)
                for row in db.execute(
                    'SELECT id,path,status,sha,size,mtime FROM catalog_photos WHERE id IN ('
                    + ','.join('?' for _ in refs)
                    + ')',
                    refs,
                )
            ]
            if refs
            else []
        )
    stacks = window.catalog.stacks() if refs else []
    # The gallery page is bounded, but avoid probing hundreds of UNC originals
    # for bulk proxy creation. Known missing status still prevents that action;
    # create_proxy validates each live source inside the background worker.
    originals_available = (
        count > 0
        and len(selected) == count
        and all(original_available(photo, live=count <= 30) for photo in selected)
    )
    return {
        'compare': count == 2,
        'survey': 2 <= count <= 30,
        'copy': count == 1,
        'stack': count >= 2 and not any(set(refs).intersection(s['photos']) for s in stacks),
        'stack_ops': any(set(refs).intersection(s['photos']) for s in stacks),
        'add_album': count > 0 and any(a['kind'] == 'album' for a in albums),
        'remove_album': count > 0 and album is not None and album['kind'] == 'album',
        'edit_album': album is not None,
        'cover': count == 1 and album is not None and album['kind'] != 'folder',
        'order': album is not None and album['kind'] == 'album' and 1 <= members <= 10000,
        'people': bool((window.report or {}).get('people')),
        'relink': count == 1,
        'search_relink': count == 1,
        'proxy_ops': count == 1,
        'proxy': originals_available,
        'auto_stack': eligible_stacks >= 2,
        'prefix': originals > 0,
        'missing': originals > 0,
        'trash': count > 0 and not window.catalog_trash,
        'restore': count > 0 and window.catalog_trash,
        'purge': count > 0 and window.catalog_trash,
        'undo': bool(undo),
        'redo': bool(redo),
        'editor': count == 1 and editor_available(window, refs[0]),
        'annotation': count == 1,
        'recommend': 1 <= count <= 30
        and len(selected) == count
        and all(original_available(photo, scanned=True) for photo in selected),
        'scan': originals > 0,
    }


def update_controls(window):
    if not hasattr(window, 'catalog_actions'):
        return
    count = len(window.selected_ids())
    idle = window.worker is None
    conditions = action_conditions(window) if idle else {}
    for key, action in window.catalog_actions.items():
        action.setEnabled(idle and conditions.get(key, True))
    window.selection_more_button.setEnabled(
        any(action.isEnabled() for action in window.catalog_menus['selection'].actions())
    )
    window.proxy_size_action.setEnabled(idle)
    i18n.set_message(
        window.selection_label, f'已选择 {count} 张' if count else '选择照片以编辑或整理'
    )
    window.editor_button.setEnabled(idle and conditions.get('editor', False))
    window.annotation_button.setEnabled(idle and conditions.get('annotation', False))
    window.recommend_button.setEnabled(idle and conditions.get('recommend', False))
    window.scan_button.setEnabled(idle and conditions.get('scan', False))
    for action in window.rating_actions + window.flag_actions:
        action.setEnabled(idle and count > 0)
    window.rating_menu_button.setEnabled(idle and count > 0)
    window.flag_menu_button.setEnabled(idle and count > 0)
    window.color_combo.setEnabled(idle and count > 0)
    if hasattr(window, 'metadata_save_button'):
        window.metadata_save_button.setEnabled(
            idle
            and count > 0
            and any(check.isChecked() for check in window.metadata_checks.values())
        )
        window.keyword_save_button.setEnabled(idle and count > 0)
    if hasattr(window, 'update_agent_controls'):
        window.update_agent_controls()
