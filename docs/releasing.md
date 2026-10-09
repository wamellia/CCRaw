# 构建

Windows CPU：运行 `build-release.cmd`。构建脚本创建隔离环境，安装锁定依赖，校验资源，运行测试，收集许可证并生成便携包。Inno Setup 存在时可生成安装程序。

macOS：在 Apple silicon 上运行 `build-macos.command`。依赖配置见 `requirements-macos.txt`。

Python 包：`python -m build`。

资源包：`python tools/build_assets.py`。校验：`python tools/fetch_assets.py --verify-only`。

GPU 环境分别使用 `requirements-gpu.txt` 或 `requirements-directml.txt`。只安装一种 ONNX Runtime。

完整构建包含 LICENSE、NOTICE、依赖与模型许可。测试平台范围以实际运行环境为准。
