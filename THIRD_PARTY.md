# 第三方组件与资源说明

本文按用途整理 CCRaw 中的第三方运行依赖、模型、字体、格式参考与构建工具，说明它们的来源、作用和分发方式。CCRaw 的项目许可见 [LICENSE](LICENSE)，项目声明见 [NOTICE](NOTICE)。组件许可与资源记录由本文链接到对应文件。

版本参考对应 `requirements/*.txt` 中的依赖环境、模型清单、本地许可文件及随附组件元数据，用于定位具体发行物；不代表上游最新版本或每个用户环境中的实际安装版本。可选组件按所选环境安装。

## 如何理解分发范围

| 类型 | 在 CCRaw 中的含义 |
|---|---|
| 基础运行依赖 | 源码安装通过 `requirements/base.txt` 获取；Windows 便携构建按所用环境收集库、动态文件及许可 |
| 包内资源 | 按用途分组位于 `ccraw/resources/`，由 Python 包与 Windows 打包配置递归收集，例如色彩管理 DLL、字体及许可文本 |
| 可选运行依赖 | 仅在选择 Agent、MCP 或 GPU 环境时安装，不能视为基础安装的一部分 |
| 可选模型与工具 | 大型模型和元数据工具由独立资源包或显式下载提供，轻量源码包不包含全部权重 |
| 格式或结构参考 | 说明读取格式或网络结构的来源，不表示分发了整个上游程序、训练框架或数据库 |
| 构建与验证工具 | 用于生成发行物或检查样片，不是用户进行基础照片编辑所必需的运行组件 |

链接使用明确的 Markdown 目标。项目主页用于定位上游；具体文件的许可仍以本地随附正文、固定模型修订和实际发行包为准。仓库归档不等于链接失效；网站的网络限制也不能作为替换或忽略许可的依据。

### 本地文件位置

所有本地链接均相对于本文件所在的项目根目录。包内资源按用途分组：

| 目录 | 内容与文件入口 |
|---|---|
| [icons/](ccraw/resources/icons/) | 应用图标、控件图标与菜单展开图标 |
| [fonts/](ccraw/resources/fonts/) | 界面与水印字体；对应授权文本见下方字体章节 |
| [generation/](ccraw/resources/generation/) | [生图模板清单](ccraw/resources/generation/generation-templates.json)及效果预览图片 |
| [watermark/](ccraw/resources/watermark/) | 水印模板、品牌图片及[素材来源元数据](ccraw/resources/watermark/watermark-source-metadata.json) |
| [models/](ccraw/resources/models/) | [Agent 模型清单](ccraw/resources/models/agent-models.json)，记录可选模型的固定来源及校验值 |
| [native/](ccraw/resources/native/) | [LittleCMS DLL](ccraw/resources/native/lcms2.dll)及[构建说明](ccraw/resources/native/LittleCMS-BUILD.txt) |
| [licenses/](ccraw/resources/licenses/) | 随包组件与字体的许可文本 |

[assets/](assets/) 保存可选运行资源清单、模型元数据及对应许可；下载后的大型权重和工具按实际安装情况提供。发行包中的 `licenses/manifest.json` 由 [collect_licenses.py](tools/packaging/collect_licenses.py) 生成，不是包内资源目录的文件。

## 一、Python 与图像处理基础

| 组件与版本参考 | 在 CCRaw 中的用途 | 许可与官方来源 |
|---|---|---|
| Python 3.12（推荐环境） | 应用语言、标准库与 Windows 启动环境。若便携包包含解释器，应保留对应 Python 发行物的许可 | PSF License Agreement 及发行物内其他声明；[官方项目](https://www.python.org/) · [Python 3.12 许可](https://docs.python.org/3.12/license.html) |
| NumPy 2.5.3 | float32 图像数组、矩阵、颜色和像素计算 | 核心为 BSD-3-Clause；当前 wheel 还声明 0BSD、MIT、Zlib、CC0-1.0 等随附代码条款，不能只用核心许可覆盖整个二进制包；[项目](https://github.com/numpy/numpy) |
| NumExpr 2.14.2 | 数组表达式计算，减少像素计算中的中间分配 | MIT；[项目](https://github.com/pydata/numexpr) |
| Numba 0.68.0 | 编译并行像素内核，用于已有图像处理路径 | BSD-2-Clause；[项目](https://github.com/numba/numba) |
| llvmlite 0.50.0 | 为 Numba 提供 LLVM 绑定与编译基础 | BSD-2-Clause，并包含 Apache-2.0 WITH LLVM-exception 等 LLVM 相关条款；应保留 wheel 内的组合许可；[项目](https://github.com/numba/llvmlite) |
| threadpoolctl 3.7.0 | 限制 BLAS 等库的线程数，减少与图像工作线程之间的竞争 | BSD-3-Clause；[项目](https://github.com/joblib/threadpoolctl) |
| OpenCV / opencv-python-headless 5.0.0.93 | 滤波、几何映射、合成、图像特征、DNN 与 OpenCL 图像运算 | 所列 OpenCV 版本为 Apache-2.0，wheel 内其他代码另有声明；[OpenCV 源码](https://github.com/opencv/opencv) · [Python wheel 项目](https://github.com/opencv/opencv-python) |
| Pillow 12.3.0 | 常见位图读写、缩略图、文字与部分水印处理 | 当前发行元数据为 **MIT-CMU**，不再笼统写作 HPND；图像编解码依赖还需保留各自声明；[项目](https://github.com/python-pillow/Pillow) |
| rawpy 0.27.1 | 将 Python 照片读取接入 LibRaw，读取 RAW 像素与基础信息 | rawpy 包装层为 MIT；这不替代其所带 LibRaw 的许可；[项目](https://github.com/letmaik/rawpy) |
| LibRaw；版本随 rawpy wheel 或构建来源确定 | RAW 解码、机型与编码支持。实际能力和许可应按所带 LibRaw 版本确认 | 官方说明提供 LGPL-2.1 或 CDDL-1.0 许可路径；[官方项目与许可说明](https://www.libraw.org/about) · [源码](https://github.com/LibRaw/LibRaw) |
| tifffile 2026.9.20 | 高位深 TIFF 与相关像素数据读写 | BSD-3-Clause；[项目](https://github.com/cgohlke/tifffile) |
| Jinja2 3.1.6 | 在受限制的模板环境中解析水印表达式 | BSD-3-Clause；[项目](https://github.com/pallets/jinja) · [许可原文](https://raw.githubusercontent.com/pallets/jinja/main/LICENSE.txt) |
| tzdata 2026.5 | 为 Windows 上的时区处理提供 IANA 时区数据 | Python 包代码为 Apache-2.0；包内 IANA 数据有其自身公共领域说明，应同时保留；[项目](https://github.com/python/tzdata) |

这些包可能带有其他库、生成代码或编译运行库。上表给出主要定位，完整文本应从实际安装的 wheel 收集，不能用一张表替代全部随附声明。

## 二、桌面界面与多媒体

| 组件与版本参考 | 在 CCRaw 中的用途 | 许可与官方来源 |
|---|---|---|
| PySide6 / Qt / Shiboken6 6.11.2 | 窗口、控件、绘制、事件、线程通信、平台服务与 Qt Python 绑定。Windows 便携包保留对应动态库 | 当前 PySide/Shiboken 元数据显示 LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only；Qt 模块与商业发行路径需按其具体条款区分；[Qt for Python](https://doc.qt.io/qtforpython-6/) · [许可说明](https://doc.qt.io/qtforpython-6/licenses.html) |
| Qt Multimedia 与其随附 FFmpeg | 音频、麦克风和媒体后端；相关 Qt wheel 可能包含 avcodec、avformat、avutil、swresample、swscale 等动态库 | FFmpeg 主要为 LGPL-2.1-or-later；启用某些可选部分会涉及 GPL。应根据实际二进制的构建选项和随附许可确认；[官方许可说明](https://ffmpeg.org/legal.html) |

Qt、Python 绑定和 FFmpeg 是不同层次的组件。记录界面库版本时，也应保留发行物内编解码器和其他第三方组件的对应许可、版本与构建来源。

## 三、色彩管理与镜头格式

### LittleCMS 2.18

- **用途：** ICC 转换、RGB float32 / uint16 色彩处理，以及显示和输出配置相关转换。
- **实际分发：** 包内包含 Windows x64 的 [lcms2.dll](ccraw/resources/native/lcms2.dll)。这是基础色彩管理引擎的构建，不包含商业插件或第三方相机、显示器 ICC 配置文件。
- **许可：** MIT。官方仓库为 [Little-CMS](https://github.com/mm2/Little-CMS)。
- **定位依据：** 构建记录固定 `lcms2.18` 与提交 `35b57af4e1a4d6d7a40ba188f9f4efb7857ff511`，记录 DLL 的 SHA-256、工具链和 ABI。
- **本地文件：** [LittleCMS-LICENSE.txt](ccraw/resources/licenses/LittleCMS-LICENSE.txt) · [LittleCMS-BUILD.txt](ccraw/resources/native/LittleCMS-BUILD.txt)。

### Lensfun XML

- **用途：** CCRaw 独立实现导入的 XML 镜头标定读取，包括支持的畸变模型；未支持的字段不视为已应用。
- **实际分发：** 不随软件附带完整 Lensfun 程序或镜头数据库。用户导入的 XML 数据库应保留其来源和条款。
- **许可区分：** Lensfun 库代码、应用程序和镜头数据库具有不同许可；其中官方镜头数据库采用 **CC BY-SA 3.0**，不等同于库代码许可。
- **官方来源：** [Lensfun 项目及许可分类](https://github.com/lensfun/lensfun)。

## 四、推理、分词与外部工具协议

| 组件与版本参考 | 在 CCRaw 中的用途与安装范围 | 许可与官方来源 |
|---|---|---|
| ONNX Runtime 1.30.0；可选 CUDA 环境固定版本见依赖文件 | 本地模型执行；可用 Provider 与 GPU 路径取决于实际发行包和驱动，不表示所有计算都在 GPU 上 | MIT；[项目](https://github.com/microsoft/onnxruntime) |
| FlatBuffers 25.12.19 | 读取与构造 ONNX 图相关的序列化数据 | Apache-2.0；[项目](https://github.com/google/flatbuffers) |
| Hugging Face tokenizers 0.23.2 | 本地 CLIP 文本分词。为可选 Agent 依赖，不是模型权重本身 | Apache-2.0；[项目](https://github.com/huggingface/tokenizers) |
| MCP Python SDK / mcp 1.30.0 | 可选 Agent 环境中的标准 stdio MCP 接口、工具 schema 与结构化结果 | MIT；[官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk) |
| CuPy；`cupy-cuda12x` 为可选环境 | 实验性的 CUDA 数组路径，是否启用取决于环境与软件配置；按需单独安装 | CuPy 为 MIT；CUDA 及其他设备组件另有自身条款；[官方项目](https://cupy.dev/) |
| Windows ML / ONNX Runtime Windows ML 发行包 | 可选 Windows GPU 配置与执行提供程序目录，按 `requirements/windows-directml.txt` 安装；按需单独安装 | Windows ML 仓库采用 MIT，运行时和目录中取得的执行提供程序仍保留各自条款；[项目](https://github.com/microsoft/WindowsML) |

不同 ONNX Runtime 发行包提供同名 Python 模块，不应在同一环境混装。CUDA、cuDNN、TensorRT 或厂商驱动的条款不由 ONNX Runtime 的 MIT 许可代替。该说明用于区分组件，不声称已随 CCRaw 分发所有可选 GPU SDK。

## 五、可选模型与权重

模型权重、网络实现、转换工具和发布 ONNX 文件可能来自不同位置，应分别记录。模型转为 ONNX 或量化后仍保留原有来源与适用许可，不能仅因格式改变就按 CCRaw 的 MIT 源码许可重新授权。

### 5.1 本地语义检索与匿名人物特征

| 模型 | 在 CCRaw 中的用途与固定来源 | 许可与本地记录 |
|---|---|---|
| OpenAI CLIP ViT-B/32 / Xenova 量化 ONNX | 本地照片与文本向量、语义检索；文本和视觉图分别加载。模型与 tokenizer 固定为修订 `d15189d7028b43f1d3e65039190477f6af591c2a` | MIT；[原始 CLIP 项目](https://github.com/openai/CLIP) · [ONNX 发布页](https://huggingface.co/Xenova/clip-vit-base-patch32) · [本地许可](ccraw/resources/licenses/CLIP-LICENSE.txt) |
| YuNet `face_detection_yunet_2023mar.onnx` | 本地人脸检测候选，来自 OpenCV Zoo 固定提交 `47534e27c9851bb1128ccc0102f1145e27f23f98` | **MIT**；以该模型目录许可为准，不直接套用 Zoo 根目录许可；[模型目录](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) · [固定版本许可](https://raw.githubusercontent.com/opencv/opencv_zoo/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet/LICENSE) · [本地许可](ccraw/resources/licenses/YuNet-LICENSE.txt) |
| SFace `face_recognition_sface_2021dec.onnx` | 本地匿名人物特征和聚类候选，来自同一固定 OpenCV Zoo 提交 | **Apache-2.0**；[模型目录](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface) · [固定版本许可](https://raw.githubusercontent.com/opencv/opencv_zoo/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_recognition_sface/LICENSE) · [本地许可](ccraw/resources/licenses/SFace-LICENSE.txt) |

以上模型由显式安装流程获取，文件大小、固定 URL 和 SHA-256 见 [agent-models.json](ccraw/resources/models/agent-models.json)。不是随轻量源码仓库直接分发全部模型；模型目录也不代表存在真实身份识别授权。

### 5.2 细节增强、降噪与自动选区

| 项目 / 资源 | 在 CCRaw 中的实际用途与来源层次 | 许可与本地记录 |
|---|---|---|
| Real-ESRGAN：`realesr-general-x4v3`、`RealESRGAN_x4plus` | 摄影超分辨率；桌面推理使用转换后的 ONNX。原始权重、转换记录和网络结构依赖分别保留 | 项目主要为 BSD-3-Clause，相关 BasicSR 结构另有 Apache-2.0 条款；[项目](https://github.com/xinntao/Real-ESRGAN) · [固定发布页](https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.2.5.0) · [权重许可](assets/RealESRGAN-LICENSE.txt) |
| BasicSR | 超分网络结构与离线转换的实现参考；不等于桌面运行时要求安装完整训练工具箱 | Apache-2.0；[项目](https://github.com/XPixelGroup/BasicSR) · [本地许可](assets/BasicSR-LICENSE.txt) |
| SkySeg | 天空分割与自动选区；原始项目和公开 ONNX 发布来源分别记录 | MIT；[原项目](https://github.com/xiongzhu666/Sky-Segmentation-and-Post-processing) · [ONNX 发布页](https://huggingface.co/JianyuanWang/skyseg) · [本地许可](assets/SkySeg-LICENSE.txt) |
| TorchVision DeepLabV3 MobileNetV3 | 人物区域分割；由相应网络及权重转换为 ONNX，桌面运行不依赖完整 PyTorch 训练环境 | BSD-3-Clause；[TorchVision](https://github.com/pytorch/vision) · [本地许可](assets/TorchVision-LICENSE.txt) |
| U-2-Net / U2NetP | 显著前景候选。原模型项目与预转换 ONNX 的发布渠道分别列出 | 模型为 Apache-2.0；[原项目](https://github.com/xuebinqin/U-2-Net) · [ONNX 发布渠道](https://github.com/danielgatis/rembg/releases/tag/v0.0.0) · [本地许可](assets/U2NET-LICENSE.txt) |
| MiDaS v2.1 small | 相对深度估计，为近景 / 远景等选区提供候选。该上游仓库已归档，固定版本来源仍保留 | MIT；[项目](https://github.com/isl-org/MiDaS) · [本地许可](assets/MiDaS-LICENSE.txt) |
| KAIR / FFDNet color | 颜色噪声与去杂色处理，使用固定模型资源；KAIR 是实现与权重来源之一 | MIT；[项目](https://github.com/cszn/KAIR) · [本地许可](assets/KAIR-LICENSE.txt) |
| DRUNet color / DPIR | 图像恢复与降噪。研究项目、实际下载权重和本地 ONNX 转换记录应同时保留 | MIT；[DPIR 项目](https://github.com/cszn/DPIR) · [本地许可](assets/DRUNet-LICENSE.txt) |
| NAFNet SIDD | 可选照片降噪，使用 `NAFNet-SIDD-width32.pth` 的对应 ONNX 转换 | MIT；[项目](https://github.com/megvii-research/NAFNet) · [本地许可](assets/NAFNet-LICENSE.txt) |

实际文件名、原始来源与哈希位于 [assets/models/](assets/models/) 下的元数据 JSON；运行资源包的文件清单见 [runtime-assets.json](assets/runtime-assets.json)。这些描述定位本项目所用资源，不将训练代码许可自动等同于所有模型、训练数据或第三方发布物的许可。

### 5.3 SenseVoice-Small 与语音导出资源

- **用途：** 本地语音识别，识别得到的文字再进入相应修图工作流。
- **模型来源：** [FunAudioLLM/SenseVoiceSmall](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)。模型权重使用 **FunASR Model Open Source License Agreement 1.1**，不按普通 MIT 或 Apache-2.0 权重描述。
- **ONNX 发布来源：** [sherpa-onnx 导出资源](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17)，固定修订 `2365baeacb507f821a0c8120fcee3d484dba7a07`，包含 `model.int8.onnx` 与 `tokens.txt`。
- **许可区分：** 导出工具和其代码的 Apache-2.0 条款，不替代原始模型权重的 FunASR 条款。
- **本地记录：** [SenseVoice-LICENSE.txt](assets/SenseVoice-LICENSE.txt) 保存当前模型许可及来源；[speech-assets.json](assets/speech-assets.json) 保存下载地址、大小和 SHA-256。

## 六、字体、品牌素材与摄影截图

### Noto Sans SC

- 用于中文界面和文字显示；包内字体为 [NotoSansSC.ttf](ccraw/resources/fonts/NotoSansSC.ttf)，可选运行资源按所选交付方式提供。
- 许可为 **SIL Open Font License 1.1**，对应字体声明保留在 [OFL.txt](assets/OFL.txt) 与 [包内 OFL.txt](ccraw/resources/licenses/OFL.txt)。
- 官方来源：[Google Fonts / Noto Sans SC](https://github.com/google/fonts/tree/main/ofl/notosanssc)。

### Roboto Regular / Light / Medium / Bold

- 包内包含四个静态 TTF，用于水印文字。随附字体版本为 **2.137（2017）**，内嵌记录为 `Copyright 2011 Google Inc. All Rights Reserved.` 及 **Apache License, Version 2.0**。
- **本地字体：** [Regular](ccraw/resources/fonts/Roboto-Regular.ttf) · [Light](ccraw/resources/fonts/Roboto-Light.ttf) · [Medium](ccraw/resources/fonts/Roboto-Medium.ttf) · [Bold](ccraw/resources/fonts/Roboto-Bold.ttf)。
- 该记录针对当前具体文件，不能用其他 Roboto 版本的许可覆盖。相关历史官方仓库为 [googlefonts/roboto-2](https://github.com/googlefonts/roboto-2)，其 [Apache-2.0 许可](https://raw.githubusercontent.com/googlefonts/roboto-2/main/LICENSE)仍可访问；仓库归档不改变已有文件的记录。
- 原始版权声明保留在字体内嵌记录与随附声明中，不改写为 CCRaw 原创字体。

### Alibaba PuHuiTi 2.0 Light / Bold

- 包内为两个未修改 OTF，用于模板水印文字。字体内嵌版权涉及 Alibaba（China）Co., Ltd.，并记录 Alibaba Design / Hanyi Fonts。
- **本地字体：** [Light](ccraw/resources/fonts/AlibabaPuHuiTi-2-45-Light.otf) · [Bold](ccraw/resources/fonts/AlibabaPuHuiTi-2-85-Bold.otf)。
- 官方入口：[阿里巴巴字体官网](https://www.alibabafonts.com/)。
- 字体采用权利人独立条款，不属于 CCRaw 的 MIT 源码授权。当前 [字体来源元数据](ccraw/resources/watermark/watermark-source-metadata.json) 保存版权、商标、制造商记录与 SHA-256，但**不等于单独的字体授权正文**；文件内也未提供可代替完整字体条款的许可证名称 / URL 字段。

### 品牌标志、模板素材与摄影截图

相机品牌标志用于水印中的品牌识别和排版，用户导入素材仍按其自身来源与权限使用。代码许可不自动授予商标权或其他独立素材权利。相关来源、版权和改编说明由 [素材元数据](ccraw/resources/watermark/watermark-source-metadata.json) 保留。

README 的摄影样片界面截图使用维护者提供并指定用于功能展示的照片，原始 NEF 和独立演示照片不随仓库分发。截图来源与范围见 [截图说明](docs/screenshots/README.md)。这与下方用于技术验证的公开 RAW 样片是不同来源。

## 七、元数据工具、打包与验证资源

| 工具 / 资源 | 实际用途与分发范围 | 许可与来源 |
|---|---|---|
| ExifTool 13.59 与随附 Perl | 读取和写入所支持的照片元数据；Windows 独立工具位于可选运行资源中，随附 Perl 模块分别保留声明 | ExifTool 按与 Perl 相同的 Artistic / GPL 条款提供，附带模块可能另有许可；[官方项目](https://exiftool.org/)；完整文本位于安装后的 `assets/exiftool/exiftool_files/` |
| PyInstaller 6.22.3 | 构建 Windows 应用，生成程序包含引导程序及必要运行时部分 | GPL-2.0-or-later 与 Bootloader Exception；部分运行时 hooks / 模块为 Apache-2.0，不能只写一个 GPL 标签；[项目](https://github.com/pyinstaller/pyinstaller) · [官方许可说明](https://pyinstaller.org/en/stable/license.html) |
| Inno Setup | 仅在使用该编译器时生成 Windows 安装程序，不是基础照片编辑的 Python 运行依赖 | 遵循所选 Inno Setup 编译器版本自身条款；[官方项目](https://jrsoftware.org/) · [当前许可全文](https://jrsoftware.org/files/is/license.txt) |
| raw.pixls.us 测试样片 | 用于 RAW 兼容性和技术验证；所采用记录标注为 CC0 的样片，原始大文件不随轻量源码或 Python 包分发 | [样片库](https://raw.pixls.us/)；具体选择仍按下载记录与各样片的实际授权确认 |

## 八、版本、许可文件与构建记录

- **版本依据：** [基础依赖](requirements/base.txt)、[锁定基础环境](requirements/base.lock.txt)、[Agent 依赖](requirements/agent.txt)、[锁定 Agent 环境](requirements/agent.lock.txt)、[Windows GPU 环境](requirements/windows-directml.txt)、[CUDA 环境](requirements/windows-cuda.txt)。范围依赖与某次构建的实际安装版本分别记录。
- **模型依据：** 固定清单、对应元数据 JSON、原始权重哈希与转换后 ONNX 哈希。模型清单和官方下载入口用于定位文件，不以“能下载”代替许可确认。
- **运行时许可收集：** [collect_licenses.py](tools/packaging/collect_licenses.py) 从实际安装的 Python 发行包中收集许可正文与声明文件，记录包名、版本、文件路径及 SHA-256，写入发行目录的 `licenses/manifest.json`。找不到正文时会保存元数据回退记录，这不等同于已取得完整许可正文。
- **单独保留的资源：** Python 环境、原生 DLL、模型、字体、模板素材和编译器等不能只依靠 Python 包元数据判断；对应原文、来源记录与构建文件仍需一起交付。
- **项目许可：** CCRaw 源码采用 [MIT License](LICENSE)，项目声明见 [NOTICE](NOTICE)。组件许可文件随对应资源保存。
