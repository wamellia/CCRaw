"""Six visible editor categories with legacy navigation kept as a route adapter."""

from PySide6.QtCore import Qt, QTimer, QPoint
from shiboken6 import isValid
from .disclosure import set_disclosure
from PySide6.QtWidgets import (
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


CATEGORIES = ('光影', '色彩', '细节', '构图', '局部', '效果')


class LegacyToolTabs(QTabWidget):
    """Keep old tool indices for callers and saved workspaces, without visible tabs.

    Once controls are regrouped, widget() resolves the actual category scroll
    area. Legacy ensureWidgetVisible() and enable/disable calls remain useful.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.routes = {}
        self.hide()

    def widget(self, index):
        return self.routes.get(index) or super().widget(index)

    def currentWidget(self):
        return self.widget(self.currentIndex())

    def setCurrentIndex(self, index):
        previous = self.currentIndex()
        super().setCurrentIndex(index)
        if index == previous and self.routes:
            self.currentChanged.emit(index)


class EditorSection(QWidget):
    def __init__(self, title, content, parent=None):
        super().__init__(parent)
        self.setObjectName('editorSection')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.header = QToolButton()
        self.header.setObjectName('editorSectionHeader')
        self.header.setText(title)
        self.header.setAccessibleName(title)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setCheckable(True)
        self.header.setMinimumHeight(34)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.content = content
        layout.addWidget(self.header)
        layout.addWidget(content)
        self.header.toggled.connect(self.set_expanded)
        self.set_expanded(False)

    def set_expanded(self, expanded):
        self.header.blockSignals(True)
        self.header.setChecked(expanded)
        self.header.blockSignals(False)
        set_disclosure(self.header, expanded)
        self.content.setVisible(expanded)


class EditorScrollArea(QScrollArea):
    """Reveal a regrouped control when an existing caller requests visibility."""

    def __init__(self, groups):
        super().__init__()
        self.groups = groups

    def ensureWidgetVisible(self, widget, xmargin=50, ymargin=50):
        for key, section in self.groups.sections.items():
            if section is widget or section.isAncestorOf(widget):
                self.groups.select(key, reveal=False)
                page = self.groups.pages[self.groups.routes[key][0]]
                QScrollArea.ensureWidgetVisible(page, widget, xmargin, ymargin)
                token = self.groups.reveal_serial

                def reveal():
                    if (
                        isValid(self.groups.owner)
                        and isValid(widget)
                        and self.groups.active == key
                        and self.groups.reveal_serial == token
                    ):
                        page.widget().layout().activate()
                        QScrollArea.ensureWidgetVisible(page, widget, xmargin, ymargin)

                QTimer.singleShot(0, reveal)
                return
        super().ensureWidgetVisible(widget, xmargin, ymargin)


class EditorGroups:
    def __init__(self, owner):
        self.owner = owner
        self.tabs = QTabWidget(owner)
        self.tabs.setObjectName('editorCategoryTabs')
        self.tabs.tabBar().hide()
        self.pages = []
        self.layouts = []
        self.sections = {}
        self.routes = {}
        self.last = {}
        self.active = None
        self.loading = False
        self.reveal_serial = 0
        for title in CATEGORIES:
            page = EditorScrollArea(self)
            page.setWidgetResizable(True)
            content = QWidget()
            layout = QVBoxLayout(content)
            layout.setContentsMargins(0, 4, 6, 12)
            layout.setSpacing(6)
            layout.addStretch()
            page.setWidget(content)
            self.tabs.addTab(page, title)
            self.pages.append(page)
            self.layouts.append(layout)
        self.tabs.currentChanged.connect(self.category_changed)

    def add(self, key, title, category, content, legacy, professional=None):
        section = EditorSection(title, content)
        section.setProperty('editor_section', key)
        layout = self.layouts[category]
        layout.insertWidget(layout.count() - 1, section)
        self.sections[key] = section
        self.routes[key] = (category, legacy, professional)
        self.last.setdefault(category, key)
        section.header.toggled.connect(lambda expanded, k=key: self.toggle(k, expanded))
        return section

    def toggle(self, key, expanded):
        if expanded:
            self.select(key)
        elif self.active == key:
            self.owner.canvas.mouseReleaseEvent(None)
            self.active = None
            self.owner._professional_sample = None
            self.owner.wb_button.setChecked(False)
            self.owner.update_tool()
            self.owner.update_overlay()
            self.owner.changed()
            self.owner.refresh_editor_reset()

    def select(self, key, *, reveal=True):
        if self.loading or key not in self.sections:
            return
        self.loading = True
        self.reveal_serial += 1
        token = self.reveal_serial
        try:
            category, legacy, professional = self.routes[key]
            if key != self.active:
                # Finish against the previous recipe/tool before choosing the
                # next operation. A mask stroke must never become retouch.
                self.owner.canvas.mouseReleaseEvent(None)
                self.owner._professional_sample = None
                self.owner.wb_button.setChecked(False)
            self.active = key
            self.last[category] = key
            self.tabs.setCurrentIndex(category)
            for other, section in self.sections.items():
                if self.routes[other][0] == category:
                    section.set_expanded(other == key)
            self.owner.tabs.setCurrentIndex(legacy)
            self.owner.tabs.routes[legacy] = self.pages[category]
            if professional is not None:
                self.owner.professional_editor.tabs.setCurrentIndex(professional)
            self.owner.tab_changed()
            self.owner.refresh_editor_reset()
            self.owner.changed()
            if reveal:

                def reveal_header():
                    if isValid(self.owner) and self.active == key and self.reveal_serial == token:
                        page = self.pages[category]
                        page.widget().layout().activate()
                        top = self.sections[key].header.mapTo(page.widget(), QPoint(0, 0)).y()
                        page.verticalScrollBar().setValue(max(0, top - 4))

                # Collapsing the previous body posts a layout request. Reveal
                # against the new geometry, rather than the old expanded height.
                QTimer.singleShot(0, reveal_header)
            state = getattr(self.owner, 'editor_section_state', None)
            if state is not None:
                state.setCurrentIndex(list(self.sections).index(key))
        finally:
            self.loading = False

    def category_changed(self, category):
        if not self.loading and category in self.last:
            self.select(self.last[category])

    def legacy_changed(self, legacy):
        if self.loading:
            return
        defaults = {
            0: 'light',
            1: 'color',
            2: 'watermark',
            3: 'detail',
            4: 'masks',
            5: 'crop',
            6: 'grading',
            7: 'effects',
            8: 'retouch',
        }
        if legacy == 9:
            self.professional_changed(self.owner.professional_editor.tabs.currentIndex())
        elif legacy in defaults:
            self.select(defaults[legacy])

    def professional_changed(self, index):
        if self.loading:
            return
        key = (
            'optics',
            'geometry',
            'calibration',
            'professional_detail',
            'bw_mixer',
            'point_color',
            'profile',
            'red_eye',
        )[index]
        self.select(key)
