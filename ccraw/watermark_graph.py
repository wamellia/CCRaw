"""Recursive visual editor for the semi-utils processor pipeline."""

import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QPushButton,
    QComboBox,
    QLabel,
    QCheckBox,
    QSizePolicy,
)
from . import watermark, i18n
from .watermark_controls import value_control, signal_value, ARRAYS, TEXT_SOURCES
from .ui.disclosure import style_hierarchy


NAMES = {
    'blur': '模糊',
    'resize': '缩放',
    'trim': '修剪空白',
    'margin': '边距',
    'margin_with_ratio': '比例填充',
    'watermark': '水印',
    'watermark_with_timestamp': '时间戳水印',
    'rounded_corner': '圆角',
    'shadow': '阴影',
    'crop': '裁剪',
    'solid_color': '纯色画布',
    'gradient_color': '渐变画布',
    'rich_text': '文字',
    'multi_rich_text': '分段文字',
    'image': '图片 / 标志',
    'alignment': '叠放对齐',
    'concat': '拼接',
    'text_segments': '文字片段',
    'left_top': '左上文字',
    'left_bottom': '左下文字',
    'right_top': '右上文字',
    'right_bottom': '右下文字',
    'legacy_watermark': '旧版标准 / 自定义水印',
    'legacy_simple': '旧版简洁水印',
    'legacy_background': '旧版背景模糊',
    'legacy_shadow': '旧版阴影',
}
FIELDS = {
    'text': '文字 / 模板',
    'font_path': '字体',
    'height': '高度',
    'width': '宽度',
    'is_bold': '粗体',
    'color': '颜色',
    'trim': '修剪文字空白',
    'blur_radius': '模糊半径',
    'scale': '缩放倍数',
    'left_margin': '左边距',
    'right_margin': '右边距',
    'top_margin': '上边距',
    'bottom_margin': '下边距',
    'margin_color': '边距颜色',
    'ratio': '填充比例 W:H',
    'delimiter_color': '分隔线颜色',
    'delimiter_width': '分隔线宽度',
    'middle_spacing': '中间间距',
    'right_alignment': '右侧对齐',
    'center_logo_height': '居中标志高度',
    'left_logo': '左标志资源',
    'right_logo': '右标志资源',
    'center_logo': '居中标志资源',
    'border_radius': '圆角半径',
    'shadow_color': '阴影颜色',
    'shadow_radius': '阴影半径',
    'offset': '裁剪偏移',
    'start_color': '起始颜色',
    'end_color': '结束颜色',
    'direction': '方向',
    'interpolate_method': '渐变方式',
    'text_alignment': '文字对齐',
    'text_spacing': '文字间距',
    'path': '图片资源',
    'horizontal_alignment': '水平对齐',
    'vertical_alignment': '垂直对齐',
    'background': '背景颜色',
    'offsets': '叠放坐标',
    'weights': '叠放顺序权重',
    'alignment': '对齐',
    'spacing': '间距',
    'select': '输入图像序号',
    'buffer_path': '输入图像资源',
    'trim_left': '修剪左侧',
    'trim_right': '修剪右侧',
    'trim_top': '修剪上侧',
    'trim_bottom': '修剪下侧',
    'font_size': '普通字大小等级',
    'bold_font_size': '粗体字大小等级',
    'bold_font_path': '粗体字体',
    'logo_position': '标志位置',
    'logo_enable': '启用标志',
    'logo': '标志资源',
    'padding_percent': '背景留白比例',
    'white_blend': '背景白色混合',
    'white_margin': '白框宽度',
    'model_color': '机身文字颜色',
    'param_color': '参数文字颜色',
}
SEGMENT_DEFAULTS = dict(
    text='文字',
    font_path='AlibabaPuHuiTi-2-45-Light.otf',
    height='{{vh(2)}}',
    is_bold=False,
    color='black',
    trim=False,
)


def defaults(name):
    schemas = getattr(watermark, 'PROCESSOR_DEFAULTS', {})
    value = copy.deepcopy(schemas.get(name, {}))
    return dict(value, processor_name=name)


class PropertyEditor(QWidget):
    def __init__(self, graph):
        super().__init__(graph)
        self.graph = graph
        self.field_widgets = {}
        self.presence_widgets = {}
        self.source_widgets = {}
        self._revision = 0
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.form_box = QWidget()
        self.form = QFormLayout(self.form_box)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.root.addWidget(self.form_box)
        self.root.addStretch()

    def show_value(self, path, value):
        self._revision += 1
        revision = self._revision
        while self.form.rowCount():
            self.form.removeRow(0)
        self.field_widgets = {}
        self.presence_widgets = {}
        self.source_widgets = {}
        if not isinstance(value, dict):
            label = QLabel('在结构树中选择一个处理步骤或文字片段。')
            label.setWordWrap(True)
            self.form.addRow(label)
            return
        if 'processor_name' in value:
            schema = defaults(value['processor_name'])
            processor_type = QComboBox()
            for name in watermark.PROCESSOR_DEFAULTS:
                processor_type.addItem(NAMES.get(name, name), name)
            processor_type.setCurrentIndex(processor_type.findData(value['processor_name']))
            processor_type.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            processor_type.currentIndexChanged.connect(
                lambda index, p=path, c=processor_type: self.graph.replace_processor(
                    p, c.currentData()
                )
            )
            self.form.addRow('步骤类型', processor_type)
        elif 'text_segments' in path:
            schema = copy.deepcopy(SEGMENT_DEFAULTS)
        else:
            schema = dict(text='', color='black', is_bold=False)
        if 'processor_name' in value:
            schema.update(select=[], buffer_path=[])
        keys = list(dict.fromkeys([*value, *schema]))
        for key in keys:
            if key in ('processor_name', 'save_buffer', 'center_height'):
                continue
            present = key in value
            initial = value.get(key, schema.get(key))
            label = QCheckBox(FIELDS.get(key, NAMES.get(key, key)))
            label.setChecked(present)
            label.setToolTip(f'{key}：勾选使用自定义值，取消勾选使用处理器默认值。')
            self.presence_widgets[key] = label
            nested = isinstance(initial, dict) or (
                isinstance(initial, list)
                and key not in ARRAYS
                and key not in ('buffer_path', 'path')
            )
            if nested:
                control = QPushButton('编辑 ' + NAMES.get(key, key))
                control.clicked.connect(
                    lambda checked=False, path=path + (key,): self.graph.select_path(path)
                )
                control.setEnabled(present)
                label.toggled.connect(
                    lambda enabled, path=path, key=key, default=initial, c=control, r=revision: (
                        self._presence(path, key, default, enabled, c, r)
                    )
                )
            else:
                control, changed = value_control(key, initial, self)
                if key == 'direction' and value.get('processor_name') == 'concat':
                    control.clear()
                    control.addItems(['horizontal', 'vertical'])
                    control.setCurrentText(str(initial))
                control.setEnabled(present)
                changed.connect(
                    lambda *args, path=path, key=key, c=control, r=revision: self._field_changed(
                        r, path, key, c, args
                    )
                )
                label.toggled.connect(
                    lambda enabled, path=path, key=key, default=initial, c=control, r=revision: (
                        self._presence(path, key, default, enabled, c, r)
                    )
                )
            control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.field_widgets[key] = control
            self.form.addRow(label, control)
            if key == 'text':
                sources = QComboBox()
                for name in TEXT_SOURCES:
                    sources.addItem(name, name)
                match = next(
                    (name for name, expression in TEXT_SOURCES.items() if expression == initial),
                    'Custom',
                )
                sources.setCurrentIndex(sources.findData(match))
                sources.currentIndexChanged.connect(
                    lambda index, c=control, s=sources: self._text_source(c, s.currentData())
                )
                self.source_widgets[key] = sources
                self.form.addRow('内容来源', sources)
        i18n.localize_window(self.form_box, i18n.language())

    def _text_source(self, control, source):
        value = TEXT_SOURCES[source]
        if value is not None:
            control.setText(value)
            control.editingFinished.emit()

    def _field_changed(self, revision, path, key, control, args):
        if revision == self._revision:
            self.graph.edit_field(path, key, signal_value(control, args))

    def _presence(self, path, key, default, enabled, control, revision):
        if revision != self._revision:
            return
        control.setEnabled(enabled)
        self.graph.set_presence(path, key, default, enabled)


class PipelineEditor(QWidget):
    changed = Signal(list)

    def __init__(self, pipeline=None, compact=False, parent=None):
        super().__init__(parent)
        self.pipeline = []
        self.current_path = ()
        self._loading = False
        self.compact = compact
        self.setMinimumWidth(0)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        heading = QLabel('处理步骤与嵌套文字')
        root.addWidget(heading)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(0)
        self.tree.setMinimumHeight(150)
        self.tree.setMaximumHeight(230 if compact else 300)
        if compact:
            self.tree.setFixedHeight(150)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(18)
        self.tree.currentItemChanged.connect(self._selected)
        root.addWidget(self.tree)
        addrow = QHBoxLayout()
        self.node_type = QComboBox()
        self.node_type.setMinimumWidth(0)
        self.node_type.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        for key in getattr(watermark, 'PROCESSOR_DEFAULTS', NAMES):
            if key in NAMES and key != 'text_segments':
                self.node_type.addItem(NAMES[key], key)
        add = QPushButton('＋ 步骤')
        add.clicked.connect(lambda: self.add_node(self.node_type.currentData()))
        addrow.addWidget(self.node_type, 1)
        addrow.addWidget(add)
        root.addLayout(addrow)
        actions = QHBoxLayout()
        self.action_buttons = {}
        for title, callback in [
            ('＋ 文字片段', self.add_segment),
            ('↑', lambda: self.move_node(-1)),
            ('↓', lambda: self.move_node(1)),
            ('删除', self.remove_node),
        ]:
            button = QPushButton(title)
            self.action_buttons[title] = button
            button.clicked.connect(callback)
            button.setAccessibleName(
                {'↑': '上移选中步骤或片段', '↓': '下移选中步骤或片段'}.get(title, title)
            )
            if title in ('↑', '↓'):
                button.setFixedWidth(36)
            elif title == '删除':
                button.setFixedWidth(60)
            actions.addWidget(button)
        root.addLayout(actions)
        self.selected_label = QLabel()
        self.selected_label.setProperty('user_content', True)
        self.selected_label.setWordWrap(True)
        root.addWidget(self.selected_label)
        self.properties = PropertyEditor(self)
        root.addWidget(self.properties)
        hint = QLabel(
            '选择树中的步骤、文字位置或片段编辑。尺寸支持 px、vh、vw 和表达式；vh / vw 基于原始照片。输入序号 0 为照片，1 为第一个步骤输出。'
        )
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.set_pipeline(pipeline or [])

    def values(self):
        return copy.deepcopy(self.pipeline)

    def _get(self, path):
        value = self.pipeline
        for key in path:
            value = value[key]
        return value

    def set_pipeline(self, pipeline):
        self.pipeline = copy.deepcopy(pipeline)
        self._build((0,) if self.pipeline else ())

    def _build(self, selected=None):
        self._loading = True
        self.tree.clear()
        self._items = {}

        def add(parent, value, path, label):
            if isinstance(value, dict) and 'processor_name' in value:
                label += ' · ' + NAMES.get(value['processor_name'], value['processor_name'])
            elif isinstance(value, dict) and 'text' in value:
                text = str(value.get('text', '')).replace('\n', ' ')
                label += ' · ' + text[:34]
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            if isinstance(value, dict) and 'text' in value and 'processor_name' not in value:
                item.setData(0, Qt.ItemDataRole.UserRole + 31, True)
            if parent is None:
                self.tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            self._items[path] = item
            if isinstance(value, dict):
                for key, child in value.items():
                    if isinstance(child, dict) or (
                        isinstance(child, list) and key not in ARRAYS and key != 'buffer_path'
                    ):
                        add(item, child, path + (key,), NAMES.get(key, key))
            elif isinstance(value, list):
                for i, child in enumerate(value):
                    if isinstance(child, (dict, list)):
                        add(item, child, path + (i,), f'{i + 1}')
            return item

        for i, node in enumerate(self.pipeline):
            add(None, node, (i,), f'{i + 1:02d}')
        self.tree.expandAll()
        style_hierarchy(self.tree)
        self._loading = False
        self.select_path(selected if selected in self._items else ((0,) if self.pipeline else ()))

    def select_path(self, path):
        path = tuple(path)
        item = self._items.get(path)
        if item:
            self.tree.setCurrentItem(item)
            # Explicit refresh also handles selecting the already selected row.
            self._selected(item, None)
        else:
            self.current_path = ()
            self.properties.show_value((), self.pipeline)
            for button in self.action_buttons.values():
                button.setEnabled(False)

    def _selected(self, item, previous):
        if self._loading or item is None:
            return
        self.current_path = tuple(item.data(0, Qt.ItemDataRole.UserRole))
        self.selected_label.setText(i18n.translate('编辑：') + item.text(0))
        value = self._get(self.current_path)
        self.properties.show_value(self.current_path, value)
        path = self.current_path
        parent = self._get(path[:-1])
        ordered = isinstance(parent, list) and isinstance(path[-1], int)
        self.action_buttons['↑'].setEnabled(ordered and path[-1] > 0)
        self.action_buttons['↓'].setEnabled(ordered and path[-1] < len(parent) - 1)
        self.action_buttons['删除'].setEnabled(len(path) > 1 or len(self.pipeline) > 1)
        self.action_buttons['＋ 文字片段'].setEnabled(
            'text_segments' in path
            or (isinstance(value, dict) and value.get('processor_name') == 'multi_rich_text')
        )

    def edit_field(self, path, key, value):
        try:
            mapping = self._get(path)
        except (KeyError, IndexError, TypeError):
            return
        if not isinstance(mapping, dict):
            return
        if key not in mapping or mapping[key] != value:
            mapping[key] = copy.deepcopy(value)
            self.changed.emit(self.values())

    def set_presence(self, path, key, default, enabled):
        mapping = self._get(path)
        if enabled:
            mapping[key] = copy.deepcopy(default)
        else:
            mapping.pop(key, None)
        self.changed.emit(self.values())
        # Structural fields affect tree reachability; keep scalar widgets alive.
        if isinstance(default, (dict, list)) and key not in ARRAYS:
            self._build(path)

    def add_node(self, name):
        if not name or len(self.pipeline) >= 64:
            return
        self.pipeline.append(defaults(name))
        self._build((len(self.pipeline) - 1,))
        self.changed.emit(self.values())

    def replace_processor(self, path, name):
        previous = self._get(path)
        node = defaults(name)
        node.update(
            {
                key: copy.deepcopy(value)
                for key, value in previous.items()
                if (key in node or key in ('select', 'buffer_path', 'save_buffer'))
                and key != 'processor_name'
            }
        )
        if name == 'multi_rich_text' and previous.get('processor_name') == 'rich_text':
            node['text_segments'] = [
                {
                    key: copy.deepcopy(previous.get(key, value))
                    for key, value in SEGMENT_DEFAULTS.items()
                }
            ]
        elif (
            name == 'rich_text'
            and previous.get('processor_name') == 'multi_rich_text'
            and previous.get('text_segments')
        ):
            node.update(copy.deepcopy(previous['text_segments'][0]))
        self._get(path[:-1])[path[-1]] = node
        self._build(path)
        self.changed.emit(self.values())

    def add_segment(self):
        path = self.current_path
        if 'text_segments' in path:
            path = path[: path.index('text_segments') + 1]
        else:
            try:
                value = self._get(path)
            except (KeyError, IndexError):
                return
            if isinstance(value, dict) and value.get('processor_name') == 'multi_rich_text':
                value.setdefault('text_segments', [])
                path += ('text_segments',)
            else:
                return
        segments = self._get(path)
        segments.append(copy.deepcopy(SEGMENT_DEFAULTS))
        self._build(path + (len(segments) - 1,))
        self.changed.emit(self.values())

    def move_node(self, delta):
        path = self.current_path
        if not path or not isinstance(path[-1], int):
            return
        siblings = self._get(path[:-1])
        i = path[-1]
        if 0 <= i + delta < len(siblings):
            siblings[i], siblings[i + delta] = siblings[i + delta], siblings[i]
            self._build(path[:-1] + (i + delta,))
            self.changed.emit(self.values())

    def remove_node(self):
        path = self.current_path
        if not path:
            return
        if len(path) == 1 and len(self.pipeline) <= 1:
            return
        parent = self._get(path[:-1])
        if isinstance(parent, list):
            parent.pop(path[-1])
            selected = path[:-1] + (max(0, path[-1] - 1),) if parent else path[:-1]
        else:
            parent.pop(path[-1], None)
            selected = path[:-1]
        self._build(selected)
        self.changed.emit(self.values())
