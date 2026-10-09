"""Shared visual tokens for Qt controls and custom painters."""

from dataclasses import dataclass
from pathlib import Path
from PySide6.QtGui import QColor, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Theme:
    background: str
    panel: str
    recessed: str
    elevated: str
    canvas: str
    text: str
    secondary: str
    disabled: str
    border: str
    hover: str
    track: str
    active_track: str
    accent: str
    selection: str
    focus: str


LIGHT = Theme(
    '#F0F3F8',
    '#F7F9FC',
    '#E9EEF5',
    '#FFFFFF',
    '#E0E6EF',
    '#172439',
    '#53627A',
    '#8E9AAE',
    '#BCC7D6',
    '#DCE7F6',
    '#A1B0C6',
    '#2563B7',
    '#1668CF',
    '#D6E7FF',
    '#1668CF',
)
DARK = Theme(
    '#1E1E1E',
    '#252526',
    '#333333',
    '#3C3C3C',
    '#1E1E1E',
    '#D4D4D4',
    '#BBBBBB',
    '#858585',
    '#454545',
    '#2A2D2E',
    '#5A5A5A',
    '#007ACC',
    '#0E639C',
    '#04395E',
    '#007ACC',
)


def tokens(widget=None):
    palette = widget.palette() if widget is not None else QApplication.palette()
    return DARK if palette.color(QPalette.Window).lightness() < 128 else LIGHT


def color(role, widget=None):
    return QColor(getattr(tokens(widget), role))


def stylesheet(t):
    selected_text = t.text if t == DARK else t.accent
    primary_hover = QColor(t.accent).lighter(120).name() if t == DARK else '#1B79EA'
    primary_pressed = QColor(t.accent).darker(125).name() if t == DARK else '#1454A4'
    check = (Path(__file__).resolve().parents[1] / 'resources' / 'check.svg').as_posix()
    assets = Path(__file__).resolve().parents[1] / 'resources'
    suffix = '-dark' if t == DARK else ''
    chevron = (assets / f'chevron{suffix}.svg').as_posix()
    up = (assets / f'chevron-up{suffix}.svg').as_posix()
    return f'''
QWidget {{ background: {t.background}; color: {t.text}; font-size: 13px; }}
QMainWindow, QDialog {{ background: {t.background}; }}
QWidget#topbar, QWidget#library, QWidget#inspector, QWidget#filmstrip {{ background: {t.panel}; }}
QWidget#topbar {{ border-bottom: 1px solid {t.border}; }}
QLabel, QCheckBox {{ background: transparent; }}
QLabel#brand {{ font-size: 18px; font-weight: 600; }}
QLabel#subtle, QStatusBar QLabel {{ color: {t.secondary}; font-size: 12px; }}
QLabel#section {{ font-size: 14px; font-weight: 600; padding: 8px 0 6px; }}
QLabel#navigator, QLabel#imagePreview {{ background: {t.canvas}; border: 0; border-radius: 8px; }}
QPushButton, QToolButton {{ background: {t.elevated}; border: 1px solid {t.border}; border-radius: 6px; padding: 6px 10px; min-height: 24px; }}
QPushButton:hover, QToolButton:hover {{ background: {t.hover}; border-color: {t.accent}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {t.recessed}; }}
QPushButton:checked, QToolButton:checked {{ background: {t.selection}; color: {t.text}; border-color: {t.accent}; font-weight: 600; }}
QPushButton:focus, QToolButton:focus {{ border-color: {t.focus}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {t.disabled}; background: {t.recessed}; border-color: {t.track}; }}
QPushButton#primary {{ background: {t.accent}; border-color: {t.accent}; color: white; padding: 6px 16px; font-weight: 600; }}
QPushButton#primary:hover {{ background: {primary_hover}; border-color: {primary_hover}; }}
QPushButton#primary:pressed {{ background: {primary_pressed}; }}
QPushButton#primary:disabled {{ background: {t.selection}; color: {t.disabled}; }}
QToolButton#toolNavigation {{ padding: 5px 6px; text-align: left; }}
QTabWidget::pane {{ border: 0; }}
QTabBar {{ background: {t.recessed}; border-radius: 8px; }}
QTabBar::tab {{ background: {t.recessed}; color: {t.secondary}; padding: 7px 9px; border: 1px solid {t.border}; border-radius: 6px; }}
QTabBar::tab:selected {{ background: {t.selection}; color: {t.text}; border-color: {t.accent}; font-weight: 600; }}
QTabBar::tab:hover:!selected {{ background: {t.hover}; }}
QSlider {{ min-height: 28px; background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: {t.track}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t.active_track}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 18px; height: 18px; margin: -8px 0; background: {t.elevated}; border: 2px solid {t.active_track}; border-radius: 10px; }}
QSlider::handle:horizontal:hover, QSlider::handle:horizontal:focus {{ border-color: {t.focus}; }}
QSlider::sub-page:horizontal:disabled {{ background: {t.track}; }}
QSlider[parameter="temperature"]::groove:horizontal {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #96B4CC,stop:0.5 {t.track},stop:1 #CEB681); }}
QSlider[parameter="tint"]::groove:horizontal {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #94B29B,stop:0.5 {t.track},stop:1 #BD9EB7); }}
QSlider[parameter="temperature"]::sub-page:horizontal, QSlider[parameter="tint"]::sub-page:horizontal {{ background: transparent; }}
QDoubleSpinBox, QSpinBox, QLineEdit, QComboBox, QTextEdit, QPlainTextEdit {{ background: {t.elevated}; border: 1px solid {t.border}; border-radius: 6px; padding: 5px 8px; min-height: 24px; selection-background-color: {t.selection}; selection-color: {t.text}; }}
QDoubleSpinBox:focus, QSpinBox:focus, QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {t.focus}; }}
QDoubleSpinBox:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {t.disabled}; }}
QComboBox {{ padding-right: 30px; }}
QComboBox::drop-down {{ border-left: 1px solid {t.border}; width: 26px; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 16px; height: 16px; }}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button, QSpinBox::up-button, QSpinBox::down-button {{ width: 18px; border-left: 1px solid {t.border}; background: {t.recessed}; }}
QDoubleSpinBox::up-arrow, QSpinBox::up-arrow {{ image: url("{up}"); width: 12px; height: 12px; }}
QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; }}
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover, QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {t.hover}; }}
QComboBox QAbstractItemView {{ background: {t.elevated}; border: 1px solid {t.border}; padding: 4px; selection-background-color: {t.selection}; selection-color: {selected_text}; }}
QCheckBox {{ spacing: 8px; padding: 6px 0; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {t.secondary}; border-radius: 4px; background: {t.elevated}; }}
QCheckBox::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url("{check}"); }}
QCheckBox::indicator:focus {{ border-color: {t.focus}; }}
QCheckBox:disabled {{ color: {t.disabled}; }}
QScrollArea {{ border: 0; background: {t.panel}; }}
QScrollArea > QWidget > QWidget {{ background: {t.panel}; }}
QScrollBar:vertical {{ background: {t.recessed}; width: 12px; margin: 2px; }}
QScrollBar:horizontal {{ background: {t.recessed}; height: 12px; margin: 2px; }}
QScrollBar::handle {{ background: {t.track}; border-radius: 3px; min-height: 24px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QListWidget {{ background: transparent; border: 0; outline: 0; }}
QListWidget::item {{ border: 2px solid transparent; border-radius: 8px; padding: 6px; }}
QListWidget::item:hover {{ background: {t.hover}; }}
QListWidget::item:selected {{ background: {t.selection}; color: {selected_text}; }}
QListWidget#filmstripList::item:selected {{ background: transparent; border-color: {t.accent}; }}
QListWidget#presetList::item:selected {{ border-color: {t.accent}; }}
QStatusBar {{ background: {t.panel}; border-top: 1px solid {t.border}; }}
QStatusBar::item {{ border: 0; }}
QSplitter::handle {{ background: {t.border}; width: 1px; }}
QMenuBar {{ background: {t.panel}; padding: 0 8px; }}
QMenuBar::item {{ background: transparent; padding: 4px 10px; }}
QMenuBar::item:selected {{ background: {t.hover}; border-radius: 6px; }}
QMenu {{ background: {t.elevated}; border: 1px solid {t.border}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 24px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t.selection}; color: {selected_text}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 8px; }}
QToolTip {{ background: {t.elevated}; color: {t.text}; border: 1px solid {t.border}; padding: 8px; }}
QProgressBar {{ background: {t.recessed}; border: 0; border-radius: 2px; text-align: center; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 2px; }}
QGroupBox {{ border: 0; margin-top: 20px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; font-weight: 600; }}
'''


STYLE = stylesheet(LIGHT)


def style_swatch(button, swatch, theme=None):
    button.setProperty('swatchColor', swatch)
    # A colored button's Window palette reflects its swatch, not the app theme.
    t = theme or tokens()
    button.setStyleSheet(
        f'QPushButton {{background: {swatch}; border: 3px solid transparent; border-radius: 18px; padding: 0; min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px;}}'
        f'QPushButton:hover {{border-color: {t.focus};}}'
        f'QPushButton:checked {{border-color: {t.text};}}'
        f'QPushButton:focus {{border-color: {t.focus};}}'
    )


def apply_theme(app, mode='light'):
    """Palette propagation keeps child dialogs and painters synchronized."""
    t = DARK if mode == 'dark' else LIGHT
    palette = QPalette()
    for role, value in {
        QPalette.Window: t.background,
        QPalette.WindowText: t.text,
        QPalette.Base: t.elevated,
        QPalette.AlternateBase: t.recessed,
        QPalette.Text: t.text,
        QPalette.Button: t.panel,
        QPalette.ButtonText: t.text,
        QPalette.ToolTipBase: t.elevated,
        QPalette.ToolTipText: t.text,
        QPalette.Highlight: t.selection,
        QPalette.HighlightedText: t.text if t == DARK else t.accent,
        QPalette.Link: t.accent,
    }.items():
        palette.setColor(role, QColor(value))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(t.disabled))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(t))
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setPixelSize(13)
    app.setFont(font)
    for widget in app.allWidgets():
        if widget.property('symbol'):
            from .icons import set_icon

            set_icon(widget, widget.property('symbol'), bool(widget.property('filled')))
        if widget.property('swatchColor'):
            style_swatch(widget, widget.property('swatchColor'), t)
        widget.update()
    return t
