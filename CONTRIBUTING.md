# 开发

使用 Python 3.12，安装 `requirements.txt` 与 `requirements-dev.txt`。每个环境只安装一种 ONNX Runtime。

```powershell
python -m ruff check ccraw tools tests main.py
python -m ruff format --check ccraw tools tests main.py
python -m pytest tests -q
```

无完整模型资源时可运行 `python -m pytest tests -m "not runtime_assets" -q`。

图像计算在线程或进程中执行；Qt 控件和 QPixmap 在 GUI 线程中使用。配方变更保留工程兼容性。新增控件接入实时预览与单次手势撤销，并使用 `ccraw/ui/theme.py` 的主题参数。
