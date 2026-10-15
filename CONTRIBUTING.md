# 贡献指南 / Contributing

在 Windows 64 位环境中使用 Python 3.12。以下命令均从仓库根目录运行；同一环境只安装一种 ONNX Runtime。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements/dev.txt
```

开发环境已包含照片编辑、Photo Agent、MCP、测试及打包依赖。仅运行应用时，可按需要选择 `requirements/base.txt` 或 `requirements/agent.txt`；各环境的范围见[依赖说明](requirements/README.md)。

检查代码格式并运行测试：

```powershell
.\.venv\Scripts\python.exe -m ruff check ccraw tools tests main.py
.\.venv\Scripts\python.exe -m ruff format --check ccraw tools tests main.py
.\.venv\Scripts\python.exe -m pytest tests -q
```

没有完整可选运行资源时，运行 `python -m pytest tests -m "not runtime_assets" -q`，并在提交说明中注明未覆盖资源相关测试。涉及模型、GPU 或发行物的变更，还应在对应运行环境中验证实际行为。

图像计算在线程或进程中执行；Qt 控件和 QPixmap 在 GUI 线程中使用。配方变更保留工程兼容性与 `ccraw.model`、`ccraw.engine` 的公共导入。新增控件接入实时预览与单次手势撤销，并使用 `ccraw/ui/theme.py` 的主题参数。提交问题或修改说明时，提供复现步骤和相关验证结果，分享日志或照片前检查私人信息。

源码职责与数据流见[架构](docs/development/architecture.md)；依赖、模型和工具按用途分别归入 `requirements/`、`assets/` 与 `tools/`。桌面包和 Python 包的构建步骤见 [Windows 构建](docs/development/releasing.md)，全部文档见[文档索引](docs/README.md)。
