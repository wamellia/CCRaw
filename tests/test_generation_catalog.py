"""Generation modes, complete prompts and isolated selection state."""

from PySide6.QtCore import Qt
import pytest
from ccraw import image_generation as gen
from test_generation_panel import generation_window  # noqa: F401

STYLE_NAMES = set(
    '人像摄影 电影写真 中国风 动漫 3D渲染 赛博朋克 CG动画 水墨画 油画 古典 水彩画 卡通平面插画 风景 港风动漫 像素风格 荧光绘图 彩铅画 手办 儿童绘画 抽象 锐笔绘画 二次元 油墨印刷 版画 莫奈 毕加索 伦勃朗 马蒂斯 巴洛克 复古动漫 绘本'.split()
)
CREATIVE_NAMES = set(
    '航拍视角 重返80年代夸张漫画 贴纸 拍照亭 Y2K数码相机风 动漫 70年代风格人像 印象派绘画 水下 3D头像 丙烯画 羊毛毡微缩模型 塔罗牌 雕像 黏土动画世界 拼装模型套件 16位街机风格 手写批注风格 美妆指南 涂鸦 你的摇头娃娃 迷你分身 剖面图 增强照片 远方情结 夜拍闪光 蓝图海报 色彩分析 漫画 动漫漫画 奇幻报纸 胶片条 超写实壁纸'.split()
)


def test_catalog_contains_both_complete_modes_and_unique_ids(tmp_path):
    styles = gen.load_templates(tmp_path, mode='style')
    creative = gen.load_templates(tmp_path, mode='creative')
    assert {t['name'] for t in styles} == STYLE_NAMES
    assert CREATIVE_NAMES <= {t['name'] for t in creative}
    assert len(styles) == 31 and len(creative) == 50
    assert len({t['id'] for t in styles + creative}) == 81
    assert all(len(t['prompt']) >= 70 for t in styles)
    assert all(t['category'] and t['preview'] for t in styles)
    assert '没有人物' in next(t['prompt'] for t in creative if t['name'] == '雕像')
    assert '透明' in next(t['prompt'] for t in creative if t['name'] == '贴纸')


def test_custom_style_survives_reload_and_old_templates_default_to_creative(tmp_path):
    from ccraw.persistence import atomic_write_json

    old = {'id': 'custom-old', 'name': '旧模板', 'prompt': '暖色胶片', 'category': '自定义'}
    atomic_write_json(tmp_path / 'templates.json', {'templates': [old]})
    saved = gen.save_template('个人风格', '自然光、水彩纸纹理', root=tmp_path, mode='style')
    assert saved in gen.load_templates(tmp_path, mode='style')
    assert any(t['id'] == old['id'] for t in gen.load_templates(tmp_path, mode='creative'))
    assert not any(t['id'] == old['id'] for t in gen.load_templates(tmp_path, mode='style'))
    gen.delete_template(saved['id'], root=tmp_path)
    assert not any(t['id'] == saved['id'] for t in gen.load_templates(tmp_path))


def test_browser_separates_duplicate_anime_names_and_filters_mode(generation_window):
    from ccraw.generation_panel import TemplateBrowser

    p = generation_window.generation_panel
    browser = TemplateBrowser(p)
    assert browser.items.count() == 50
    browser.search.setText('动漫')
    assert browser.items.count() == 2
    browser.modes.setCurrentIndex(1)
    assert browser.items.count() == 7  # names and the animation category match
    assert all(browser.items.item(i).data(Qt.UserRole)['mode'] == 'style' for i in range(7))
    browser.search.clear()
    assert browser.items.count() == 31
    browser.category.setCurrentText('艺术家')
    assert browser.items.count() == 4
    browser.use_selected()
    p.use_template(browser.selected)
    assert p.mode.currentData() == 'style'
    assert p.prompt.toPlainText() == browser.selected['prompt']
    assert p.template_label.text() == browser.selected['name']
    browser.close()


def test_switch_mode_preserves_editable_prompts_and_selection(generation_window):
    p = generation_window.generation_panel
    creative = next(t for t in gen.load_templates(mode='creative') if t['name'] == '贴纸')
    style = next(t for t in gen.load_templates(mode='style') if t['name'] == '水彩画')
    p.use_template(creative)
    p.prompt.appendPlainText('补充：紫色围巾')
    edited = p.prompt.toPlainText()
    p.use_template(style)
    assert p.mode.currentData() == 'style'
    p.mode.setCurrentIndex(0)
    assert p.template_name == '贴纸' and p.prompt.toPlainText() == edited
    p.mode.setCurrentIndex(1)
    assert p.template_name == '水彩画' and p.prompt.toPlainText() == style['prompt']


def test_custom_generation_is_independent_of_template_selection(generation_window):
    p = generation_window.generation_panel
    p.use_template(next(t for t in gen.load_templates() if t['id'] == 'creative-wallpaper'))
    p.subject.setCurrentIndex(2)
    creative_prompt = p.prompt.toPlainText()
    custom_index = p.mode.findData('custom')
    assert custom_index >= 0
    p.mode.setCurrentIndex(custom_index)
    assert p.prompt.toPlainText() == '' and p.selected_template is None
    assert not p.browse.isVisibleTo(p.widget())
    assert not p.template_label.isVisibleTo(p.widget())
    assert not p.effect_preview.isVisibleTo(p.widget())
    assert not p.subject.isVisibleTo(p.widget())
    p.prompt.setPlainText('蓝色陶瓷茶杯，窗边自然光，方形构图')
    p.mode.setCurrentIndex(0)
    assert p.prompt.toPlainText() == creative_prompt and p.subject.currentIndex() == 2
    p.use_template(next(t for t in gen.load_templates() if t['id'] == 'style-watercolor'))
    p.mode.setCurrentIndex(custom_index)
    assert p.prompt.toPlainText() == '蓝色陶瓷茶杯，窗边自然光，方形构图'
    assert p.selected_template is None and p.template_name == ''


@pytest.mark.parametrize('mode', ['creative', 'style', 'custom'])
def test_clear_resets_only_current_generation_input(generation_window, mode):
    p = generation_window.generation_panel
    p.mode.setCurrentIndex(p.mode.findData('custom'))
    p.prompt.setPlainText('保留的自定义输入')
    p.use_template(next(t for t in gen.load_templates() if t['id'] == 'style-watercolor'))
    p.use_template(next(t for t in gen.load_templates() if t['id'] == 'creative-wallpaper'))
    p.subject.setCurrentIndex(2)
    p.mode.setCurrentIndex(p.mode.findData(mode))
    other = 'style' if mode != 'style' else 'custom'
    expected_other = '保留的自定义输入' if other == 'custom' else p.mode_state['style']['prompt']
    p.reference.setCurrentIndex(p.reference.findData('none'))
    p.count.setValue(3)
    p.size.setCurrentText('4K')
    task = p.store.enqueue('保留的记录', '历史提示词')
    p.store.update(task['id'], 'cancelled')
    assert p.clear.isVisibleTo(p.widget())
    p.clear.click()
    assert p.active_mode == mode and p.mode.currentData() == mode
    assert p.prompt.toPlainText() == '' and p.selected_template is None
    assert p.template_name == '' and p.subject_text == ''
    assert p.subject.count() == 0 and not p.subject.isVisibleTo(p.widget())
    assert not p.template_label.isVisibleTo(p.widget())
    assert not p.effect_preview.isVisibleTo(p.widget())
    assert p.count.value() == 3 and p.size.currentText() == '4K'
    assert p.reference.currentData() == 'none'
    assert p.store.get(task['id'])['prompt'] == '历史提示词'
    p.enqueue()
    assert len(p.store.history()) == 1 and '提示词' in p.status.text()
    p.mode.setCurrentIndex(p.mode.findData(other))
    assert p.prompt.toPlainText() == expected_other
    p.mode.setCurrentIndex(p.mode.findData(mode))
    assert p.prompt.toPlainText() == '' and p.selected_template is None


def test_custom_generation_sends_exact_prompt_and_restores_history(generation_window):
    from ccraw.generation_panel import HistoryDialog
    from test_image_generation import Service
    from test_ui import wait_until

    p = generation_window.generation_panel
    server = Service()
    try:
        p.settings = server.settings()
        p.mode.setCurrentIndex(p.mode.findData('custom'))
        p.reference.setCurrentIndex(p.reference.findData('none'))
        p.prompt.setPlainText('蓝色陶瓷茶杯，窗边自然光，方形构图')
        p.enqueue()
        wait_until(lambda: not p.busy and not p.pending)
        assert len(p.store.history()) == 1, p.status.text()
        task = p.store.get(p.store.history()[0]['id'])
        assert task['status'] == 'completed' and task['mode'] == 'custom'
        assert server.calls[0][2]['prompt'] == '蓝色陶瓷茶杯，窗边自然光，方形构图'
        assert 'image' not in server.calls[0][2]
        assert gen.GenerationStore(p.store.root).get(task['id'])['mode'] == 'custom'
        p.use_template(next(t for t in gen.load_templates() if t['id'] == 'creative-wallpaper'))
        d = HistoryDialog(p)
        d.restore_prompt()
        assert p.mode.currentData() == 'custom'
        assert p.prompt.toPlainText() == '蓝色陶瓷茶杯，窗边自然光，方形构图'
        assert p.selected_template is None and not p.subject.isVisibleTo(p.widget())
        assert not p.template_label.isVisibleTo(p.widget())
        d.close()
    finally:
        p.shutdown()
        server.close()


def test_wallpaper_requests_subject_before_submitting(generation_window):
    p = generation_window.generation_panel
    wallpaper = next(t for t in gen.load_templates(mode='creative') if t['name'] == '超写实壁纸')
    p.use_template(wallpaper)
    p.reference.setCurrentIndex(p.reference.findData('none'))
    p.enqueue()
    assert not p.store.history() and '拍摄主体' in p.status.text()
    assert p.subject.isVisibleTo(p.widget())
    p.subject.setCurrentIndex(2)
    assert '{拍摄主体}' not in p.prompt.toPlainText()
    assert p.subject.currentData() in p.prompt.toPlainText()


def test_photo_template_requires_reference_and_consent(generation_window):
    p = generation_window.generation_panel
    template = next(t for t in gen.load_templates(mode='creative') if t['name'] == '增强照片')
    p.use_template(template)
    p.reference.setCurrentIndex(p.reference.findData('none'))
    p.enqueue()
    assert not p.store.history() and '参考图' in p.status.text()


def test_history_restores_generation_mode_without_stale_subject_state(generation_window):
    from ccraw.generation_panel import HistoryDialog

    p = generation_window.generation_panel
    wallpaper = next(t for t in gen.load_templates() if t['name'] == '超写实壁纸')
    p.use_template(wallpaper)
    task = p.store.enqueue('油画', '暖色油画', mode='style')
    p.store.update(task['id'], 'cancelled')
    d = HistoryDialog(p)
    d.restore_prompt()
    assert p.mode.currentData() == 'style'
    assert p.prompt.toPlainText() == '暖色油画'
    assert p.selected_template is None and not p.subject.isVisibleTo(p.widget())
    assert p.template_name == '油画'
    d.close()


def test_generation_history_migrates_existing_database_without_losing_tasks(tmp_path):
    import sqlite3

    with sqlite3.connect(tmp_path / 'history.sqlite') as connection:
        connection.execute(
            'CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL, reference TEXT NOT NULL, recipe TEXT, size TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL DEFAULT "", result TEXT NOT NULL DEFAULT "", created REAL NOT NULL, initialized INTEGER NOT NULL DEFAULT 1)'
        )
        connection.execute(
            'INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            ('old', '旧记录', '胶片', '', None, '2K', 'completed', '', '', 1, 1),
        )
    store = gen.GenerationStore(tmp_path)
    assert store.get('old')['mode'] == 'creative'
    new = store.enqueue('水彩', '水彩纸', mode='style')
    assert gen.GenerationStore(tmp_path).get(new['id'])['mode'] == 'style'
    assert len(store.history()) == 2


def test_style_queue_snapshots_mode_and_prompt(generation_window):
    from test_image_generation import Service
    from test_ui import wait_until

    p = generation_window.generation_panel
    server = Service()
    try:
        p.settings = server.settings()
        style = next(t for t in gen.load_templates(mode='style') if t['name'] == '油画')
        p.use_template(style)
        p.reference.setCurrentIndex(p.reference.findData('none'))
        p.enqueue()
        p.mode.setCurrentIndex(0)
        p.prompt.setPlainText('后来编辑的创意提示词')
        wait_until(lambda: not p.busy and not p.pending)
        task = p.store.get(p.store.history()[0]['id'])
        assert task['mode'] == 'style' and task['prompt'] == style['prompt']
        assert server.calls[0][2]['prompt'] == style['prompt']
        assert task['status'] == 'completed'
    finally:
        p.shutdown()
        server.close()


def test_builtin_previews_are_complete_distinct_and_decodable(app):
    import hashlib
    from PySide6.QtGui import QImage

    seen = set()
    for template in gen.load_templates():
        path = gen.RESOURCES / template['preview']
        assert path.is_file(), template['name']
        image = QImage(str(path))
        assert not image.isNull() and min(image.width(), image.height()) >= 160, template['name']
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest not in seen, template['name']
        seen.add(digest)
    assert len(seen) == 81


def test_sticker_preview_has_real_transparent_background():
    from PIL import Image

    template = next(t for t in gen.load_templates() if t['id'] == 'creative-stickers')
    with Image.open(gen.RESOURCES / template['preview']) as image:
        assert image.mode == 'RGBA'
        assert image.getchannel('A').getextrema() == (0, 255)


def test_selected_style_displays_generated_effect_image(generation_window):
    p = generation_window.generation_panel
    template = next(t for t in gen.load_templates() if t['id'] == 'style-watercolor')
    p.use_template(template)
    assert not p.effect_preview.pixmap().isNull()
    assert p.effect_preview.isVisibleTo(p.widget())
    assert p.template_label.text() == '水彩画'


def test_subject_selection_preserves_user_rewritten_prompt(generation_window):
    p = generation_window.generation_panel
    p.use_template(next(t for t in gen.load_templates() if t['id'] == 'creative-wallpaper'))
    p.prompt.setPlainText('阴天自然光，保留湿润石阶细节。')
    p.subject.setCurrentIndex(2)
    assert '阴天自然光，保留湿润石阶细节。' in p.prompt.toPlainText()
    assert p.subject.currentData() in p.prompt.toPlainText()


def test_saving_template_keeps_reference_and_unresolved_subject_requirements(
    generation_window, monkeypatch
):
    from PySide6.QtWidgets import QInputDialog

    p = generation_window.generation_panel
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **kw: ('保存的版本', True))
    p.use_template(next(t for t in gen.load_templates() if t['id'] == 'creative-enhance'))
    p.save_template()
    p.reference.setCurrentIndex(p.reference.findData('none'))
    p.enqueue()
    assert p.selected_template.get('requires_reference') is True
    assert not p.store.history() and '参考图' in p.status.text()
    p.use_template(next(t for t in gen.load_templates() if t['id'] == 'creative-wallpaper'))
    p.save_template()
    assert p.subject.isVisibleTo(p.widget())
    p.enqueue()
    assert not p.store.history() and '拍摄主体' in p.status.text()
    saved = gen.load_templates(p.store.root)
    restored = next(t for t in saved if t['id'] == p.selected_template['id'])
    assert len(restored['subject_choices']) == 6
    p.use_template(restored)
    p.subject.setCurrentIndex(1)
    p.save_template()
    assert '{拍摄主体}' not in p.prompt.toPlainText()
    assert not p.subject.isVisibleTo(p.widget())


def test_literal_subject_placeholder_cannot_be_sent_from_custom_or_history(generation_window):
    p = generation_window.generation_panel
    p.selected_template = None
    p.prompt.setPlainText('微距照片，主体为 {拍摄主体}')
    p.reference.setCurrentIndex(p.reference.findData('none'))
    p.enqueue()
    assert not p.store.history() and '拍摄主体' in p.status.text()


def test_browser_initial_size_fits_high_dpi_screen(generation_window, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtCore import QRect
    from ccraw.generation_panel import TemplateBrowser

    screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 1280, 680))
    monkeypatch.setattr(TemplateBrowser, 'screen', lambda self: screen)
    d = TemplateBrowser(generation_window.generation_panel)
    assert d.width() <= 1280 - 48 and d.height() <= 680 - 64
    d.show()
    d.layout().activate()
    assert d.use.geometry().bottom() < d.height()
    d.close()


def test_generate_action_stays_visible_while_browsing_compact_panel(generation_window, app):
    from PySide6.QtCore import QPoint

    w = generation_window
    w.resize(1180, 780)
    w.library_tabs.setCurrentIndex(1)
    w.instruction_tabs.setCurrentIndex(1)
    p = w.generation_panel
    p.use_template(next(t for t in gen.load_templates() if t['id'] == 'style-watercolor'))
    app.processEvents()
    for value in (p.verticalScrollBar().minimum(), p.verticalScrollBar().maximum()):
        p.verticalScrollBar().setValue(value)
        app.processEvents()
        top = p.generate.mapTo(p, QPoint(0, 0))
        bottom = p.generate.mapTo(p, p.generate.rect().bottomRight())
        assert p.rect().contains(top) and p.rect().contains(bottom)
        assert p.generate.isVisibleTo(w)
