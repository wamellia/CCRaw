"""Visible catalog controls shared by the Agent workspace; no chat is required."""

import json
from pathlib import Path

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QMenu,
    QComboBox,
    QCheckBox,
    QLineEdit,
    QPlainTextEdit,
    QInputDialog,
    QFileDialog,
    QDialog,
    QDialogButtonBox,
    QScrollArea,
    QListWidget,
    QListWidgetItem,
)

from .repository import Catalog
from .rules import describe_rules
from .views import CompareDialog, SurveyDialog
from .manager import CatalogWindow, open_catalog_for_editor, install_catalog  # noqa: F401
from ..ui.layout import FlowLayout
from . import controls


def _button(text, callback):
    button = QPushButton(text)
    button.setMinimumHeight(32)
    button.clicked.connect(callback)
    return button


class CatalogWindowMixin:
    def install_catalog(self, photo_layout):
        self.catalog = Catalog(self.project)
        self.catalog_rules = {'all': []}
        self.catalog_album = None
        self.catalog_trash = False
        self.catalog_preset = None
        self.catalog_dialogs = []
        navigation_panel = QWidget()
        navigation = FlowLayout(navigation_panel, margin=0)
        self.navigation = QComboBox(self)
        self.navigation.setProperty('user_content', True)
        self.navigation.setMinimumWidth(130)
        self.navigation.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.navigation.setMinimumContentsLength(12)
        self.navigation.currentIndexChanged.connect(self.catalog_navigation_changed)
        self.navigation.hide()
        controls.install_navigation(self)
        self.sort_combo = QComboBox()
        for title, key in [
            ('导入时间', 'import'),
            ('拍摄时间', 'capture'),
            ('编辑时间', 'edit'),
            ('文件名', 'name'),
            ('评级', 'rating'),
            ('大小', 'size'),
        ]:
            self.sort_combo.addItem(title, key)
        preferred = (
            self.project.preferences()
            .get('ccraw.settings', {})
            .get('project', {})
            .get('album_sort', 'import')
        )
        self.sort_combo.setCurrentIndex(max(0, self.sort_combo.findData(preferred)))
        self.sort_combo.currentIndexChanged.connect(lambda _: self.catalog_refilter())
        navigation.addWidget(self.sort_combo)
        self.sort_descending = QCheckBox('降序')
        self.sort_descending.toggled.connect(lambda _: self.catalog_refilter())
        navigation.addWidget(self.sort_descending)
        navigation.addWidget(_button('筛选条件', self.edit_filter_dialog))
        photo_layout.insertWidget(1, navigation_panel)
        self.filter_summary = QLabel('筛选：全部照片')
        self.filter_summary.setWordWrap(True)
        photo_layout.insertWidget(2, self.filter_summary)
        culling_panel = QWidget()
        culling = FlowLayout(culling_panel, margin=0)
        rating_menu = QMenu(self)
        self.rating_actions = []
        for rating in range(6):
            action = QAction('未评级' if rating == 0 else '★' * rating, self)
            action.triggered.connect(lambda _checked=False, value=rating: self.rate_selected(value))
            rating_menu.addAction(action)
            self.rating_actions.append(action)
        self.rating_menu_button = QPushButton('评级 0–5')
        self.rating_menu_button.setMinimumHeight(32)
        self.rating_menu_button.setToolTip('选中照片后评级；也可按键盘 0–5。')
        self.rating_menu_button.setMenu(rating_menu)
        culling.addWidget(self.rating_menu_button)
        self.flag_actions = []
        flag_menu = QMenu(self)
        for label, flag in [('保留 P', 'pick'), ('拒绝 X', 'reject'), ('清除 U', 'none')]:
            action = QAction(label, self)
            action.triggered.connect(lambda _checked=False, value=flag: self.flag_selected(value))
            flag_menu.addAction(action)
            self.flag_actions.append(action)
        self.flag_menu_button = QPushButton('保留 / 拒绝')
        self.flag_menu_button.setMinimumHeight(32)
        self.flag_menu_button.setMenu(flag_menu)
        culling.addWidget(self.flag_menu_button)
        self.auto_advance = QCheckBox('自动前进')
        culling.addWidget(self.auto_advance)
        self.color_combo = QComboBox()
        for title, value in [
            ('无标签', ''),
            ('红', 'red'),
            ('黄', 'yellow'),
            ('绿', 'green'),
            ('蓝', 'blue'),
            ('紫', 'purple'),
        ]:
            self.color_combo.addItem(title, value)
        self.color_combo.activated.connect(
            lambda _: self.catalog_apply_state(color_label=self.color_combo.currentData())
        )
        culling.addWidget(self.color_combo)
        photo_layout.insertWidget(3, culling_panel)
        self.gallery.culling_key.connect(
            lambda key, value: self.catalog_apply_state(**{key: value})
        )
        self.gallery.itemSelectionChanged.connect(self.refresh_metadata)
        self.refresh_catalog_navigation()
        self._install_metadata()
        self._install_management()
        self.suggestion_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.suggestion_list.customContextMenuRequested.connect(self.suggestion_menu)

    def catalog_refilter(self):
        if not hasattr(self, 'catalog'):
            return
        self.offset = 0
        self.refresh_gallery()

    def refresh_catalog_navigation(self):
        selected = self.navigation.currentData() or 'all'
        blocker = QSignalBlocker(self.navigation)
        self.navigation.clear()
        for title, value in [
            ('全部照片', 'all'),
            ('最近导入', 'recent'),
            ('已编辑', 'edited'),
            ('缺失原片', 'missing'),
            ('回收站', 'trash'),
        ]:
            self.navigation.addItem(title, value)
        albums = self.catalog.albums()
        by_id = {a['id']: a for a in albums}
        for album in albums:
            depth, parent = 0, album['parent']
            while parent in by_id and depth < 8:
                depth += 1
                parent = by_id[parent]['parent']
            prefix = (
                '文件夹'
                if album['kind'] == 'folder'
                else '智能'
                if album['kind'] == 'smart'
                else '相册'
            )
            self.navigation.addItem(
                '  ' * depth + prefix + ' · ' + album['name'], 'album:' + album['id']
            )
        for preset in self.catalog.filter_presets():
            self.navigation.addItem('筛选 · ' + preset['name'], 'preset:' + preset['id'])
        self.navigation.setCurrentIndex(max(0, self.navigation.findData(selected)))
        blocker.unblock()
        controls.refresh_navigation(self)

    def catalog_navigation_changed(self, *_):
        value = self.navigation.currentData() or 'all'
        tree_blocker = QSignalBlocker(self.catalog_navigation_tree)
        item = self.catalog_navigation_items.get(value)
        if item:
            self.catalog_navigation_tree.setCurrentItem(item)
        tree_blocker.unblock()
        self.catalog_album = value[6:] if value.startswith('album:') else None
        self.catalog_preset = value[7:] if value.startswith('preset:') else None
        self.catalog_trash = value == 'trash'
        self.result_ids = None
        self.offset = 0
        if value == 'recent':
            self.sort_combo.setCurrentIndex(self.sort_combo.findData('import'))
            self.sort_descending.setChecked(True)
        if self.catalog_preset:
            preset = next(
                p for p in self.catalog.filter_presets() if p['id'] == self.catalog_preset
            )
            self.catalog_rules = preset['rules']
            blockers = [QSignalBlocker(self.sort_combo), QSignalBlocker(self.sort_descending)]
            self.sort_combo.setCurrentIndex(self.sort_combo.findData(preset['sort']))
            self.sort_descending.setChecked(bool(preset['descending']))
            for blocker in blockers:
                blocker.unblock()
        if value == 'missing':
            self.catalog.refresh_missing()
        self.refresh_gallery()

    def catalog_query(self, *, limit=200, offset=None):
        rules = [self.catalog_rules]
        value = self.navigation.currentData() or 'all'
        if value == 'edited':
            rules.append({'field': 'edited', 'op': 'eq', 'value': True})
        elif value == 'missing':
            rules.append({'field': 'status', 'op': 'eq', 'value': 'missing'})
        description = describe_rules({'all': rules})
        if self.catalog_album:
            description = self.navigation.currentText() + '；' + description
        if self.catalog_trash:
            description = '回收站；' + description
        self.filter_summary.setText(
            '筛选：' + description[:140] + ('…' if len(description) > 140 else '')
        )
        self.filter_summary.setToolTip(description)
        return self.catalog.query(
            rules={'all': rules},
            album=self.catalog_album,
            sort=self.sort_combo.currentData(),
            descending=self.sort_descending.isChecked(),
            limit=limit,
            offset=self.offset if offset is None else offset,
            trash=self.catalog_trash,
            photo_refs=self.result_ids,
            include_collapsed=self.result_ids is not None,
        )

    def catalog_apply_state(self, **changes):
        if self.worker is not None:
            return
        refs = self.selected_ids()
        if not refs:
            self.statusBar().showMessage('请选择照片。')
            return
        row = self.gallery.currentRow()
        visible = [
            self.gallery.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.gallery.count())
        ]
        current = refs[0]
        if current in visible:
            row = visible.index(current)
        next_ref = visible[min(row + 1, len(visible) - 1)] if visible else None
        advance = self.auto_advance.isChecked() and len(refs) == 1
        next_page = False
        if advance and row == len(visible) - 1:
            following = self.catalog_query(limit=1, offset=self.offset + len(visible))
            if following['photos']:
                next_ref = following['photos'][0]['id']
                next_page = True
        try:
            self.catalog.set_state(refs, **changes)
            if next_page:
                boundary = self.offset + len(visible) - 1
                shifted = self.catalog_query(limit=1, offset=boundary)['photos']
                if shifted and shifted[0]['id'] == next_ref:
                    self.offset = boundary // 200 * 200
                else:
                    self.offset += 200
            self.refresh_gallery()
            if advance:
                for i in range(self.gallery.count()):
                    if self.gallery.item(i).data(Qt.ItemDataRole.UserRole) == next_ref:
                        self.gallery.clearSelection()
                        self.gallery.setCurrentRow(i)
                        break
                else:
                    if self.gallery.count():
                        self.gallery.setCurrentRow(min(max(row, 0), self.gallery.count() - 1))
            self.statusBar().showMessage('选片状态已保存；可撤销。')
        except (ValueError, OSError) as error:
            self.statusBar().showMessage(str(error))

    def rate_selected(self, rating):
        self.catalog_apply_state(rating=rating)

    def flag_selected(self, flag):
        self.catalog_apply_state(flag=flag)

    def _install_metadata(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        explanation = QLabel('勾选字段后批量应用；多个值保持原状，空文字仅在勾选时清空。')
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        grid = QGridLayout()
        self.metadata_fields, self.metadata_checks = {}, {}
        for i, (key, label) in enumerate(
            [
                ('title', '标题'),
                ('description', '说明'),
                ('author', '作者'),
                ('copyright', '版权'),
                ('location', '地点'),
                ('person_count', '人数'),
            ]
        ):
            check = QCheckBox(label)
            value = QLineEdit()
            value.setMaxLength(8000)
            grid.addWidget(check, i, 0)
            grid.addWidget(value, i, 1)
            self.metadata_checks[key], self.metadata_fields[key] = check, value
        layout.addLayout(grid)
        keyword = QHBoxLayout()
        self.keyword_input = QLineEdit()
        self.keyword_input.setPlaceholderText('层级关键词以 / 分层，多个关键词用逗号分隔')
        keyword.addWidget(self.keyword_input, 1)
        self.keyword_mode = QComboBox()
        for title, mode in [('追加', 'append'), ('移除', 'remove'), ('替换', 'replace')]:
            self.keyword_mode.addItem(title, mode)
        keyword.addWidget(self.keyword_mode)
        self.keyword_save_button = _button('应用关键词', self.save_keywords)
        keyword.addWidget(self.keyword_save_button)
        layout.addLayout(keyword)
        self.metadata_save_button = _button('保存勾选元数据', self.save_metadata)
        layout.addWidget(self.metadata_save_button)
        for check in self.metadata_checks.values():
            check.toggled.connect(self.update_catalog_controls)
        self.metadata_exif = QPlainTextEdit()
        self.metadata_exif.setReadOnly(True)
        layout.addWidget(self.metadata_exif, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        self.tabs.addTab(scroll, '照片信息')

    def refresh_metadata(self):
        if not hasattr(self, 'metadata_fields'):
            return
        refs = self.selected_ids()
        if not refs:
            for value in self.metadata_fields.values():
                value.clear()
            self.metadata_exif.clear()
            return
        values = self.catalog.metadata_values(refs)
        for key, field in self.metadata_fields.items():
            entry = values[key]
            field.setText('' if entry['mixed'] or entry['value'] is None else str(entry['value']))
            field.setPlaceholderText(
                '多个值' if entry['mixed'] else '未知' if key == 'person_count' else ''
            )
            self.metadata_checks[key].setChecked(False)
        self.keyword_input.setText(
            '' if values['keywords']['mixed'] else ', '.join(values['keywords']['value'])
        )
        self.keyword_input.setPlaceholderText(
            '多个关键词值；选择追加/移除/替换' if values['keywords']['mixed'] else '旅行/海边, 候选'
        )
        self.metadata_exif.setPlainText(
            json.dumps(self.project.photo(refs[0])['facts'], ensure_ascii=False, indent=2)
        )

    def save_metadata(self):
        if self.worker is not None or not self.selected_ids():
            return
        values = {
            key: field.text()
            for key, field in self.metadata_fields.items()
            if self.metadata_checks[key].isChecked()
        }
        if 'person_count' in values:
            try:
                values['person_count'] = (
                    int(values['person_count']) if values['person_count'].strip() else None
                )
            except ValueError:
                self.statusBar().showMessage('人数需要整数或留空表示未知。')
                return
        if not values:
            return
        self.catalog_action(lambda: self.catalog.edit_metadata(self.selected_ids(), values))

    def save_keywords(self):
        if self.worker is not None or not self.selected_ids():
            return
        values = [
            v.strip() for v in self.keyword_input.text().replace('，', ',').split(',') if v.strip()
        ]
        self.catalog_action(
            lambda: self.catalog.edit_keywords(
                self.selected_ids(), values, mode=self.keyword_mode.currentData()
            )
        )

    def _install_management(self):
        controls.install_management(self)

    def update_catalog_controls(self):
        controls.update_controls(self)

    def catalog_operation_available(self, key):
        return self.worker is None and controls.action_conditions(self).get(key, True)

    def catalog_action(self, operation):
        if self.worker is not None:
            return None
        try:
            result = operation()
            self.refresh_catalog_navigation()
            self.refresh_gallery()
            self.refresh_records()
            self.refresh_metadata()
            if self.report is not None:
                if 'inferred_people' in self.report:
                    self.report['people'] = self.catalog.apply_cluster_corrections(
                        self.report['inferred_people']
                    )
                with self.project.connect() as db:
                    decisions = {
                        row['id']: row['decision']
                        for row in db.execute('SELECT id,decision FROM suggestion_decisions')
                    }
                for kind in ('duplicates', 'similar', 'quality', 'events', 'people'):
                    for group in self.report.get(kind, []):
                        group['decision'] = decisions.get(self.catalog.suggestion_id(kind, group))
                self.refresh_suggestions()
            self.catalog_status.setText('管理动作已保存；原片保留。')
            self.statusBar().showMessage(self.catalog_status.text())
            return result
        except (ValueError, OSError) as error:
            self.catalog_status.setText(str(error))
            self.statusBar().showMessage(str(error))
            return None

    def new_album(self, kind='album'):
        if not self.catalog_operation_available('new_album'):
            return
        name, ok = QInputDialog.getText(self, '新建相册', '名称')
        if not ok:
            return
        rules = self.catalog_rules if kind == 'smart' else None
        parent = None
        if self.catalog_album:
            current = next(
                (a for a in self.catalog.albums() if a['id'] == self.catalog_album), None
            )
            parent = (
                current['id']
                if current and current['kind'] == 'folder'
                else current['parent']
                if current
                else None
            )
        self.catalog_action(
            lambda: self.catalog.create_album(name, kind=kind, rules=rules, parent=parent)
        )

    def add_to_album(self):
        if not self.catalog_operation_available('add_album'):
            return
        albums = [a for a in self.catalog.albums() if a['kind'] == 'album']
        if not albums:
            self.statusBar().showMessage('先建立普通相册。')
            return
        names = [a['name'] + ' · ' + a['id'][:6] for a in albums]
        name, ok = QInputDialog.getItem(self, '添加到相册', '相册', names, 0, False)
        if ok:
            self.catalog_action(
                lambda: self.catalog.add_members(
                    albums[names.index(name)]['id'], self.selected_ids()
                )
            )

    def remove_from_album(self):
        if not self.catalog_operation_available('remove_album'):
            return
        if self.catalog_album:
            self.catalog_action(
                lambda: self.catalog.remove_members(self.catalog_album, self.selected_ids())
            )

    def edit_filter_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('实际执行的筛选条件')
        dialog.resize(640, 480)
        layout = QVBoxLayout(dialog)
        match = QComboBox()
        for label, key in [
            ('满足全部条件', 'all'),
            ('满足任一条件', 'any'),
            ('不满足以下任一条件', 'none'),
        ]:
            match.addItem(label, key)
        current = next((key for key in ('all', 'any', 'none') if key in self.catalog_rules), 'all')
        match.setCurrentIndex(match.findData(current))
        layout.addWidget(match)
        row = QHBoxLayout()
        field = QComboBox()
        names = {
            'rating': '评级',
            'flag': '保留 / 拒绝',
            'color_label': '颜色标签',
            'capture': '拍摄日期时间',
            'camera': '相机',
            'lens': '镜头',
            'iso': 'ISO',
            'format': '格式',
            'person_count': '人数',
            'keyword': '关键词',
            'edited': '已编辑',
            'name': '文件名',
            'size': '文件大小',
            'album': '所属相册',
        }
        for key, label in names.items():
            field.addItem(label, key)
        row.addWidget(field)
        operator = QComboBox()
        for label, key in [
            ('等于', 'eq'),
            ('不等于', 'ne'),
            ('至少', 'ge'),
            ('至多', 'le'),
            ('包含', 'contains'),
        ]:
            operator.addItem(label, key)
        row.addWidget(operator)
        value = QLineEdit()
        value.setPlaceholderText('评级 0–5；flag: pick/reject/none；日期 YYYY-MM-DD')
        row.addWidget(value, 1)
        layout.addLayout(row)
        conditions = QListWidget()

        def describe(rule):
            if 'field' in rule:
                return (
                    names.get(rule['field'], rule['field'])
                    + ' '
                    + rule['op']
                    + ' '
                    + str(rule['value'])
                )
            return '已有嵌套条件组 · ' + str(len(next(iter(rule.values()), []))) + ' 个条件'

        for rule in self.catalog_rules.get(current, [self.catalog_rules]):
            item = QListWidgetItem(describe(rule))
            item.setData(Qt.ItemDataRole.UserRole, rule)
            conditions.addItem(item)
        layout.addWidget(conditions, 1)
        error = QLabel()
        layout.addWidget(error)

        def add():
            try:
                text = value.text().strip()
                key = field.currentData()
                parsed = (
                    int(text)
                    if key in ('rating', 'iso', 'person_count', 'size')
                    else text.casefold() in ('true', 'yes', '1', '是')
                    if key == 'edited'
                    else text
                )
                if key == 'album':
                    matches = [
                        a for a in self.catalog.albums() if a['name'] == text or a['id'] == text
                    ]
                    if len(matches) != 1:
                        raise ValueError('所属相册请使用唯一相册名称或相册 ID。')
                    parsed = matches[0]['id']
                rule = {'field': key, 'op': operator.currentData(), 'value': parsed}
                self.catalog.query(rules=rule, limit=1)
                item = QListWidgetItem(describe(rule))
                item.setData(Qt.ItemDataRole.UserRole, rule)
                conditions.addItem(item)
                error.clear()
            except (ValueError, TypeError) as exc:
                error.setText(str(exc))

        actions = QHBoxLayout()
        actions.addWidget(_button('添加条件', add))
        actions.addWidget(_button('移除条件', lambda: conditions.takeItem(conditions.currentRow())))
        actions.addWidget(_button('清空条件', conditions.clear))
        layout.addLayout(actions)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )

        def apply():
            try:
                rules = {
                    match.currentData(): [
                        conditions.item(i).data(Qt.ItemDataRole.UserRole)
                        for i in range(conditions.count())
                    ]
                }
                self.catalog.query(rules=rules, limit=1)
                self.catalog_rules = rules
                self.catalog_preset = None
                self.filter_summary.setText('筛选：' + describe_rules(rules))
                self.result_ids = None
                self.catalog_refilter()
                dialog.accept()
            except (ValueError, TypeError) as exc:
                error.setText(str(exc))

        buttons.accepted.connect(apply)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def edit_album_dialog(self):
        if not self.catalog_operation_available('edit_album'):
            return
        current = next((a for a in self.catalog.albums() if a['id'] == self.catalog_album), None)
        if not current:
            self.statusBar().showMessage('先在导航中选择相册。')
            return
        action, ok = QInputDialog.getItem(
            self, '相册设置', '动作', ['重命名', '移动到文件夹', '以当前筛选更新智能规则'], 0, False
        )
        if not ok:
            return
        if action == '重命名':
            name, ok = QInputDialog.getText(self, '相册名称', '新名称', text=current['name'])
            if ok:
                self.catalog_action(lambda: self.catalog.update_album(current['id'], name=name))
        elif action == '移动到文件夹':
            folders = [
                a
                for a in self.catalog.albums()
                if a['kind'] == 'folder' and a['id'] != current['id']
            ]
            labels = ['顶层'] + [a['name'] + ' · ' + a['id'][:6] for a in folders]
            label, ok = QInputDialog.getItem(self, '相册位置', '父文件夹', labels, 0, False)
            if ok:
                parent = None if label == '顶层' else folders[labels.index(label) - 1]['id']
                self.catalog_action(lambda: self.catalog.update_album(current['id'], parent=parent))
        elif current['kind'] == 'smart':
            self.catalog_action(
                lambda: self.catalog.update_album(current['id'], rules=self.catalog_rules)
            )
        else:
            self.statusBar().showMessage('只有智能相册支持动态规则。')

    def album_cover(self):
        if not self.catalog_operation_available('cover'):
            return
        if self.catalog_album and len(self.selected_ids()) == 1:
            self.catalog_action(
                lambda: self.catalog.update_album(self.catalog_album, cover=self.selected_ids()[0])
            )

    def album_order(self):
        if not self.catalog_operation_available('order'):
            return
        if not self.catalog_album:
            return
        with self.project.connect() as db:
            rows = db.execute(
                'SELECT m.photo_ref,v.path FROM album_members m JOIN catalog_photos v ON v.id=m.photo_ref WHERE m.album_id=? ORDER BY m.position,m.photo_ref LIMIT 10001',
                (self.catalog_album,),
            ).fetchall()
            album = db.execute(
                'SELECT kind FROM albums WHERE id=?', (self.catalog_album,)
            ).fetchone()
        if not album or album['kind'] != 'album' or len(rows) > 10000:
            self.statusBar().showMessage(
                '自定义顺序适用于最多 10000 张的普通相册；更大的相册请使用分页排序。'
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('拖动自定义相册顺序')
        layout = QVBoxLayout(dialog)
        items = QListWidget()
        items.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        for photo in rows:
            item = QListWidgetItem(Path(photo['path']).name)
            item.setData(Qt.ItemDataRole.UserRole, photo['photo_ref'])
            items.addItem(item)
        layout.addWidget(items)
        save = _button(
            '保存顺序',
            lambda: self.catalog_action(
                lambda: self.catalog.reorder_album(
                    self.catalog_album,
                    [items.item(i).data(Qt.ItemDataRole.UserRole) for i in range(items.count())],
                )
            ),
        )
        layout.addWidget(save)
        dialog.exec()

    def purge_selected(self):
        if not self.catalog_operation_available('purge'):
            return
        refs = self.selected_ids()
        if not refs or any(self.catalog.state(ref)['removed_at'] is None for ref in refs):
            self.statusBar().showMessage('请在工程回收站选择需要清空的引用。')
            return
        path, _ = QFileDialog.getSaveFileName(
            self, '先保存数据库备份；然后清空这些管理记录，原片保留', '', 'SQLite (*.sqlite3)'
        )
        if path:
            self.catalog_action(lambda: self.catalog.purge_removed(refs, path))

    def save_filter_preset(self):
        if not self.catalog_operation_available('save_filter'):
            return
        name, ok = QInputDialog.getText(self, '筛选预设', '名称')
        if ok:
            self.catalog_action(
                lambda: self.catalog.save_filter(
                    name,
                    self.catalog_rules,
                    sort=self.sort_combo.currentData(),
                    descending=self.sort_descending.isChecked(),
                )
            )

    def compare_selected(self):
        if not self.catalog_operation_available('compare'):
            return
        refs = self.selected_ids()
        if len(refs) != 2:
            self.statusBar().showMessage('Compare 请选择两张不同的照片。')
            return None
        dialog = CompareDialog(self.project, [self.project.photo(ref) for ref in refs], self)
        self.catalog_dialogs.append(dialog)
        dialog.show()
        return dialog

    def survey_selected(self):
        if not self.catalog_operation_available('survey'):
            return
        refs = self.selected_ids()
        if not 2 <= len(refs) <= 30:
            self.statusBar().showMessage('Survey 请选择 2–30 张候选。')
            return None
        dialog = SurveyDialog(self.project, [self.project.photo(ref) for ref in refs], self)
        self.catalog_dialogs.append(dialog)
        dialog.show()
        return dialog

    def stack_selected(self):
        if not self.catalog_operation_available('stack'):
            return
        return self.catalog_action(lambda: self.catalog.create_stack(self.selected_ids()))

    def stack_dialog(self):
        if not self.catalog_operation_available('stack_ops'):
            return
        refs = set(self.selected_ids())
        stacks = [s for s in self.catalog.stacks() if refs.intersection(s['photos'])]
        if not stacks:
            self.statusBar().showMessage('选中已有堆叠成员。')
            return
        stack = stacks[0]
        choice, ok = QInputDialog.getItem(
            self, '堆叠操作', '动作', ['折叠', '展开', '设选中照片为封面', '解散'], 0, False
        )
        if ok:
            if choice == '解散':
                self.catalog_action(lambda: self.catalog.dissolve_stack(stack['id']))
            elif choice == '设选中照片为封面':
                self.catalog_action(
                    lambda: self.catalog.update_stack(stack['id'], cover=self.selected_ids()[0])
                )
            else:
                self.catalog_action(
                    lambda: self.catalog.update_stack(stack['id'], collapsed=choice == '折叠')
                )

    def auto_stack_dialog(self):
        if not self.catalog_operation_available('auto_stack'):
            return
        seconds, ok = QInputDialog.getDouble(
            self, '自动堆叠', '拍摄间隔秒数；未知拍摄时间保持独立', 2, 0, 3600, 2
        )
        if ok:
            self.catalog_action(lambda: self.catalog.auto_stack(seconds=seconds))

    def copy_selected(self):
        if not self.catalog_operation_available('copy'):
            return
        if len(self.selected_ids()) != 1:
            self.statusBar().showMessage('请选择一张照片建立虚拟副本。')
            return
        self.catalog_action(lambda: self.catalog.create_virtual_copy(self.selected_ids()[0]))

    def cluster_dialog(self):
        if not self.catalog_operation_available('people'):
            return
        groups = (self.report or {}).get('people', [])
        if not groups:
            self.statusBar().showMessage('先扫描并生成匿名人物簇。')
            return
        operation, ok = QInputDialog.getItem(
            self, '人物簇纠正', '动作', ['合并簇', '拆分选中照片', '移除选中误识别'], 0, False
        )
        if not ok:
            return
        text, ok = QInputDialog.getText(
            self,
            '人物簇纠正',
            '输入簇 ID，多个以逗号分隔：\n'
            + '\n'.join(g['id'] + ' · ' + g['label'] for g in groups[:20]),
        )
        if ok:
            clusters = [v.strip() for v in text.split(',') if v.strip()]
            op = {'合并簇': 'merge', '拆分选中照片': 'split', '移除选中误识别': 'remove'}[operation]
            self.catalog_action(
                lambda: self.catalog.correct_cluster(
                    op, clusters, photos=self.selected_ids() if op != 'merge' else None
                )
            )
            self.report = analysis_suggestions(self.project)
            self.refresh_suggestions()

    def suggestion_menu(self, position):
        from PySide6.QtWidgets import QMenu

        item = self.suggestion_list.itemAt(position)
        if item is None:
            return
        group = item.data(Qt.ItemDataRole.UserRole + 1)
        if not group:
            return
        kind, record = group
        menu = QMenu(self)
        for title, decision in [
            ('保留建议', 'keep'),
            ('忽略建议', 'ignore'),
            ('标记已处理', 'resolved'),
        ]:
            action = menu.addAction(title)
            action.setEnabled(
                self.worker is None and bool(record.get('photos') or record.get('photo_id'))
            )
            action.triggered.connect(
                lambda _checked=False, d=decision: self.decide_group(kind, record, d)
            )
        if kind == 'events':
            action = menu.addAction('按事件建立相册')
            action.setEnabled(self.worker is None and bool(record.get('photos')))
            action.triggered.connect(lambda: self.event_album(record))
        if record.get('best_candidate'):
            menu.addAction('选中建议最佳候选').triggered.connect(
                lambda: self.select_best_candidate(record)
            )
        menu.exec(self.suggestion_list.viewport().mapToGlobal(position))

    def select_best_candidate(self, group):
        best = group.get('best_candidate')
        if not best:
            return
        self.result_ids = group['photos']
        self.offset = self.result_ids.index(best) // 200 * 200
        self.refresh_gallery()
        self.gallery.clearSelection()
        for index in range(self.gallery.count()):
            if self.gallery.item(index).data(Qt.ItemDataRole.UserRole) == best:
                self.gallery.setCurrentRow(index)
                break

    def decide_group(self, kind, group, decision):
        if self.worker is not None:
            return
        self.catalog_action(lambda: self.catalog.decide_suggestion(kind, group, decision))
        group['decision'] = decision
        self.refresh_suggestions()

    def event_album(self, group):
        if self.worker is not None or not group.get('photos'):
            return

        def action():
            album = self.catalog.create_album(group.get('label', '事件'))
            for offset in range(0, len(group['photos']), 500):
                self.catalog.add_members(album, group['photos'][offset : offset + 500])

        self.catalog_action(action)

    def relink_selected(self):
        if not self.catalog_operation_available('relink'):
            return
        refs = self.selected_ids()
        if len(refs) != 1:
            self.statusBar().showMessage('请选择一张缺失照片。')
            return
        path, _ = QFileDialog.getOpenFileName(self, '重新关联原片')
        if path:
            photo = self.project.photo(refs[0])
            confirmed = bool(photo['sha'])
            if not confirmed:
                from PySide6.QtWidgets import QMessageBox

                confirmed = (
                    QMessageBox.question(
                        self, '确认候选', '没有原始 hash；此候选只能按名称和大小验证。确认重关联？'
                    )
                    == QMessageBox.StandardButton.Yes
                )
            if confirmed:
                self.catalog_action(
                    lambda: self.catalog.relink(
                        refs[0], path, confirm_name_size=not bool(photo['sha'])
                    )
                )

    def search_relink_folder(self):
        if not self.catalog_operation_available('search_relink'):
            return
        refs = self.selected_ids()
        if len(refs) != 1:
            return
        folder = QFileDialog.getExistingDirectory(self, '查找重关联候选')
        if folder:
            self.start_work(
                '检索重关联候选',
                lambda cancel, emit: self.catalog.find_relink_candidates(
                    refs[0], folder, cancel=cancel
                ),
                lambda result: self.show_relink_candidates(refs[0], result),
            )

    def show_relink_candidates(self, ref, candidates):
        if not candidates:
            self.statusBar().showMessage('没有找到候选。')
            return
        names = [
            ('hash 已验证 · ' if c['verified'] else '待确认 · ') + c['path'] for c in candidates
        ]
        choice, ok = QInputDialog.getItem(self, '重关联候选', '多候选不会自动选择', names, 0, False)
        if ok:
            candidate = candidates[names.index(choice)]
            self.catalog_action(
                lambda: self.catalog.relink(
                    ref, candidate['path'], confirm_name_size=not candidate['verified']
                )
            )

    def relink_prefix_dialog(self):
        if not self.catalog_operation_available('prefix'):
            return
        old, ok = QInputDialog.getText(self, '替换目录前缀', '旧目录')
        if not ok:
            return
        new = QFileDialog.getExistingDirectory(self, '新目录')
        if new:
            self.catalog_action(lambda: self.catalog.relink_prefix(old, new))

    def proxy_selected(self):
        if not self.catalog_operation_available('proxy'):
            return
        refs = self.selected_ids()
        if not refs:
            return
        max_edge = self.proxy_edge.value()

        def operation(cancel, emit):
            result = []
            for i, ref in enumerate(refs):
                result.append(self.catalog.create_proxy(ref, max_edge=max_edge, cancel=cancel))
                emit(
                    'scan',
                    {
                        'percent': round((i + 1) / len(refs) * 100),
                        'text': f'离线代理 {i + 1}/{len(refs)}',
                    },
                )
            return result

        self.start_work('创建 16 位离线代理', operation, lambda _: self.refresh_gallery())

    def proxy_dialog(self):
        if not self.catalog_operation_available('proxy_ops'):
            return
        refs = self.selected_ids()
        if len(refs) != 1:
            return
        ref = refs[0]
        try:
            proxy = self.catalog.proxy(ref)
            status = self.catalog.proxy_status(ref)
            proxy_file = bool(proxy and Path(proxy['path']).is_file())
        except (ValueError, OSError):
            status, proxy_file = 'missing', False
        with self.project.connect() as db:
            proxy_reference = bool(
                db.execute('SELECT 1 FROM proxies WHERE photo_ref=?', (ref,)).fetchone()
            )
        choices = [
            ('打开离线编辑', controls.editor_available(self, ref, allow_proxy=True)),
            ('明确输出低分辨率副本', status in ('ready', 'offline')),
            ('迁移代理存储', proxy_file),
            ('清理无引用代理缓存', True),
            ('移除代理引用（可撤销）', proxy_reference),
        ]
        dialog = QInputDialog(self)
        dialog.setWindowTitle('代理操作')
        dialog.setLabelText(f'代理状态：{status}')
        dialog.setComboBoxItems([label for label, _available in choices])
        dialog.setComboBoxEditable(False)
        combo = dialog.findChild(QComboBox)
        for index, (_label, available) in enumerate(choices):
            combo.model().item(index).setEnabled(available)
        combo.setCurrentIndex(next(i for i, (_label, available) in enumerate(choices) if available))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        choice, available = choices[combo.currentIndex()]
        if not available or self.worker is not None:
            return
        if choice == '打开离线编辑':
            self.open_editor(allow_proxy=True)
        elif choice == '移除代理引用（可撤销）':
            self.catalog_action(lambda: self.catalog.remove_proxy(refs[0]))
        elif choice == '迁移代理存储':
            folder = QFileDialog.getExistingDirectory(
                self, '选择代理存储目录；原片与可撤销的旧代理保留'
            )
            if folder:
                self.catalog_action(lambda: self.catalog.relocate_proxy(refs[0], folder))
        elif choice == '清理无引用代理缓存':
            self.catalog_action(self.catalog.cleanup_proxies)
            self.statusBar().showMessage('已清理无引用缓存；有效代理和撤销/重做需要的代理保留。')
        else:
            path, _ = QFileDialog.getSaveFileName(self, '明确输出代理分辨率 PNG', '', 'PNG (*.png)')
            if path:
                self.catalog_action(lambda: self.catalog.export_proxy(refs[0], path))

    def backup_catalog(self):
        if not self.catalog_operation_available('backup'):
            return
        path, _ = QFileDialog.getSaveFileName(self, 'SQLite 安全备份', '', 'SQLite (*.sqlite3)')
        if path:
            self.catalog_action(lambda: self.catalog.backup(path))


def analysis_suggestions(project):
    from ..photo_agent.analysis import suggestions

    return suggestions(project)
