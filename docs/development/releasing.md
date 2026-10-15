# Windows 构建与发行

所有命令从仓库根目录运行。使用 Windows 64 位与 Python 3.12，并准备所需的可选运行资源。源码开发环境的安装和检查见[贡献指南](../../CONTRIBUTING.md)。

## 依赖环境

| 文件 | 用途 |
|---|---|
| [base.txt](../../requirements/base.txt) | 基础编辑器的直接依赖 |
| [agent.txt](../../requirements/agent.txt) | 包含基础依赖的可选 Agent / MCP 环境 |
| [dev.txt](../../requirements/dev.txt) | 测试、格式检查及打包工具 |
| [base.lock.txt](../../requirements/base.lock.txt) | 固定版本的基础开发与构建环境 |
| [agent.lock.txt](../../requirements/agent.lock.txt) | 默认桌面构建所用的固定 Agent 环境 |
| [windows-directml.txt](../../requirements/windows-directml.txt) | 可选 Windows GPU 运行环境及安装说明 |
| [windows-cuda.txt](../../requirements/windows-cuda.txt) | 可选 NVIDIA CUDA 运行环境及安装说明 |

不同 ONNX Runtime 发行包提供同名模块，同一环境只保留一种。GPU 环境的驱动、运行库及替换步骤以对应依赖文件为准。默认 Windows 构建使用 `requirements/agent.lock.txt`；基础源码安装可只用 `requirements/base.txt`。

## 桌面发行物

```powershell
.\build-release.cmd
```

入口调用 `tools/packaging/build_release.ps1`：创建隔离环境、安装锁定依赖、检查依赖和资源、运行测试、收集许可证，随后构建 PyInstaller 应用、运行冻结程序检查并生成便携包和校验文件。大型运行资源与 Agent 本地模型需先安装；缺失资源会使校验失败。`tools/packaging/prepare_agent_models.py` 只从本机已校验的模型缓存复制清单中的五个公共文件，不包含用户配置或凭据。

直接调用 PowerShell 脚本可以指定已准备的解释器、资源包或 Inno Setup 编译器：

```powershell
.\tools\packaging\build_release.ps1 -PythonPath ".\.venv\Scripts\python.exe" -AssetArchive "CCRaw-RuntimeAssets.zip" -Iscc "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

这些路径是示例，按实际安装情况替换。使用 `-PythonPath` 时脚本不会替该环境安装锁定依赖，需要提前安装 Agent 及构建依赖。只有传入可用的 `-Iscc` 才编译安装程序。PyInstaller 收集 nanobot、tokenizers 及对应包元数据。完整分发包含项目 LICENSE、NOTICE 和实际收集的依赖、模型许可；资源条款见[第三方组件](../../THIRD_PARTY.md)。

## Python 包与运行资源

```powershell
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe tools/assets/build_assets.py
.\.venv\Scripts\python.exe tools/assets/fetch_assets.py --verify-only
```

第一条命令生成 Python wheel 和源码归档。第二条从已准备的 `assets/` 生成独立运行资源包并更新清单；它不下载缺失的模型。第三条按清单校验运行资源。大型模型不随轻量 Python 包分发。

本地 Agent 模型在软件的「本地模型」页面或通过 `python tools/models/agent_models.py` 单独安装，不扩大传统运行资源包；用途及校验见[模型资源](../user/models.md)。构建产物的验证范围以实际执行的平台、资源和测试结果为准。

## 发布入口

`publish-release.cmd` 调用 `tools/release/publish_release.ps1`。本地构建与发布是分开的操作；发布脚本的参数和实际上传文件应与准备交付的发行物对应。项目文档入口见[文档索引](../README.md)。
