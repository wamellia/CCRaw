# 构建

Windows CPU：运行 `build-release.cmd`。构建脚本创建隔离环境，安装锁定依赖，校验资源，运行测试，收集许可证并生成便携包。Inno Setup 存在时可生成安装程序。

Python 包：`python -m build`。

资源包：`python tools/build_assets.py`。校验：`python tools/fetch_assets.py --verify-only`。

GPU 环境分别使用 `requirements-gpu.txt` 或 `requirements-directml.txt`。只安装一种 ONNX Runtime。

完整构建包含 LICENSE、NOTICE、依赖与模型许可。测试平台范围以实际运行环境为准。

默认 Windows 构建使用 `requirements-agent-lock.txt`，在传统编辑器依赖上加入固定版本的 Photo Agent 运行依赖。自选 `-PythonPath` 环境也需要安装 `requirements-agent.txt`。PyInstaller 收集 nanobot、tokenizers 及对应包元数据。本地 Agent 模型通过软件内「本地模型」或 `tools/agent_models.py` 单独安装，不扩大传统运行资源包。基础源码安装仍可只使用 `requirements.txt`。
