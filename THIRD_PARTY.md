# 第三方依赖

CCRaw 的源码许可不替代依赖许可。便携包保留动态库，Qt / PySide 可替换；应用源码和构建脚本同时交付。

| 组件 | 主要许可 / 来源 |
|---|---|
| Photo-Butler 提示词模板及预览图片 | ISC，https://github.com/Jokerealm/Photo-Butler；ccraw/resources/Photo-Butler-LICENSE.txt |
| semi-utils 模板、处理器及相机品牌图片 | Apache-2.0，https://github.com/leslievan/semi-utils，现行模板与改编的处理器来自 b1cf1ce655e9452a78a88f465d4387f84cf6740e；旧布局参考 build1521（2f0915465b9a2be780235f24e4b20dc43f65e8b0）。许可见 ccraw/resources/Semi-Utils-LICENSE.txt。处理器移除了上游应用入口、日志与任意路径读取，接入 CCRaw 浮点照片、色彩管理和预览；品牌图保留原边距。 |
| Alibaba PuHuiTi 2.0 Light / Bold | Alibaba（China）Co., Ltd.，Alibaba Design / Hanyi Fonts；两个原始 OTF 文件未修改。使用阿里巴巴字体自身条款，不属于项目 Apache-2.0 或 MIT 授权。官方字体页面：https://done.alibabadesign.com/puhuiti2.0；字体内嵌版权、商标、制造商记录与 SHA-256 保存在 ccraw/resources/watermark-source-metadata.json；该记录不替代字体许可 |
| Roboto Regular / Light / Medium / Bold | Copyright 2011 Google Inc. All Rights Reserved，字体内嵌 Apache-2.0 许可；原始字体取自 semi-utils config/fonts，未修改 |
| LightCraft 架构与专业工具设计参考 | MIT OR Apache-2.0，https://github.com/storytold/lightcraft，参考提交 76b5377868d912db69a777bda253a5b1b96b22bd 的 pipeline、develop、catalog、engine 命令、设置及 MCP 设计；CCRaw 以 Python 独立实现，未复制或打包 Rust 代码及品牌素材 |
| LittleCMS 2.18 | MIT，https://github.com/mm2/Little-CMS；ccraw/resources/lcms2.dll 为 Windows x64 静态运行库构建；许可与来源见 LittleCMS-LICENSE.txt 和 LittleCMS-BUILD.txt |
| Lensfun XML 格式参考 | https://github.com/lensfun/lensfun；CCRaw 独立实现 XML 标定读取，不随软件分发 Lensfun 程序或数据库；用户导入数据库应保留其来源许可（上游数据库通常为 CC-BY-SA-3.0） |
| Python | PSF，https://www.python.org/ |
| nanobot-ai 0.3.5（可选 Agent 核心循环） | MIT，https://github.com/HKUDS/nanobot；依赖许可由构建工具从已安装包收集 |
| MCP Python SDK（可选 Agent stdio 接口，由 nanobot-ai 依赖提供） | MIT，https://github.com/modelcontextprotocol/python-sdk；依赖许可由构建工具从已安装包收集 |
| Hugging Face tokenizers（可选本地 CLIP tokenizer） | Apache-2.0，https://github.com/huggingface/tokenizers |
| OpenAI CLIP / Xenova 量化 ONNX | MIT，https://github.com/openai/CLIP；https://huggingface.co/Xenova/clip-vit-base-patch32；ccraw/resources/CLIP-LICENSE.txt |
| OpenCV Zoo YuNet 人脸检测 | MIT，https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet；ccraw/resources/YuNet-LICENSE.txt |
| OpenCV Zoo SFace 匿名人脸特征 | Apache-2.0，https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface；ccraw/resources/SFace-LICENSE.txt |
| NumExpr / Numba / llvmlite | MIT / BSD-2-Clause / BSD-3-Clause and LLVM terms; see collected license texts |
| NumPy | BSD，https://numpy.org/ |
| threadpoolctl | BSD-3-Clause，https://github.com/joblib/threadpoolctl；限制 BLAS 线程，避免与像素处理线程竞争 |
| OpenCV | Apache-2.0 等，https://opencv.org/ |
| Pillow | HPND 等，https://python-pillow.org/ |
| Jinja2 | BSD-3-Clause，https://github.com/pallets/jinja；使用受限制的模板环境解析水印表达式 |
| rawpy | MIT，https://github.com/letmaik/rawpy |
| LibRaw | LGPL-2.1 / CDDL 双许可，https://www.libraw.org/ |
| PySide6 / Qt / Shiboken（含 Qt Multimedia 及其 FFmpeg 插件） | LGPL-3.0 / GPL / 商业许可及组件自身许可，https://www.qt.io/ |
| FFmpeg 动态库（Qt Multimedia 随附：avcodec、avformat、avutil、swresample、swscale） | LGPL-2.1 或更高版本，https://ffmpeg.org/ ；动态链接，可替换 |
| tifffile | BSD-3-Clause，https://github.com/cgohlke/tifffile |
| Noto Sans SC | SIL Open Font License 1.1，https://github.com/google/fonts/tree/main/ofl/notosanssc |
| ONNX Runtime / FlatBuffers | MIT / Apache-2.0，https://github.com/microsoft/onnxruntime / https://github.com/google/flatbuffers |
| Real-ESRGAN 摄影权重 | BSD-3-Clause，https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.2.5.0；assets/RealESRGAN-LICENSE.txt |
| BasicSR 网络结构参考 | Apache-2.0，https://github.com/XPixelGroup/BasicSR；assets/BasicSR-LICENSE.txt |
| SkySeg 天空模型 | MIT，https://github.com/xiongzhu666/Sky-Segmentation-and-Post-processing；ONNX 发布 https://huggingface.co/JianyuanWang/skyseg；assets/SkySeg-LICENSE.txt |
| TorchVision DeepLabV3 MobileNetV3 人物模型 | BSD-3-Clause，https://github.com/pytorch/vision；assets/TorchVision-LICENSE.txt |
| U-2-Net / U2NetP 显著前景模型 | Apache-2.0，https://github.com/xuebinqin/U-2-Net；ONNX 发布 https://github.com/danielgatis/rembg/releases/tag/v0.0.0；assets/U2NET-LICENSE.txt |
| Inno Setup 安装程序 | Inno Setup 自身许可，允许商业与非商业使用；https://jrsoftware.org/；https://jrsoftware.org/files/is/license.txt（使用该编译器时由发行维护者收集其许可） |
| ExifTool 13.59 与随附 Perl | Perl 相同条款（Artistic / GPL）及依赖自身许可；https://exiftool.org/；完整许可在 assets/exiftool/exiftool_files/ |
| MiDaS v2.1 small 相对深度模型 | MIT，Intel ISL，https://github.com/isl-org/MiDaS；assets/MiDaS-LICENSE.txt |
| KAIR / FFDNet 彩色去杂色 | MIT，Kai Zhang，https://github.com/cszn/KAIR；assets/KAIR-LICENSE.txt |
| DRUNet color | MIT，Kai Zhang，https://github.com/cszn/DPIR；assets/DRUNet-LICENSE.txt |
| NAFNet SIDD | MIT，Megvii，https://github.com/megvii-research/NAFNet；assets/NAFNet-LICENSE.txt |
| SenseVoice-Small 语音识别模型 | FunASR 模型开源协议 1.1，FunAudioLLM / 阿里巴巴集团，https://huggingface.co/FunAudioLLM/SenseVoiceSmall；ONNX 导出 k2-fsa / sherpa-onnx（Apache-2.0），https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17；assets/SenseVoice-LICENSE.txt |
| 可选 CuPy | MIT，https://cupy.dev/ |
| PyInstaller 引导程序 | GPL-2.0 及引导程序例外条款，https://pyinstaller.org/ |

构建时 tools/collect_licenses.py 从实际安装的依赖收集许可文本与校验清单，便携目录包含 `licenses/`。字体许可证在 `assets/OFL.txt`。测试用的 Sony ARW 样片来自 https://raw.pixls.us/，记录为 CC0，仅用于验证，未将大体积原始样片加入便携包。

智能水印使用 semi-utils 的相机品牌图片，保留原始留白和上述来源声明；用户导入的标志优先。新增真实 RAW 测试照片来自 raw.pixls.us 的 CC0 样片库，不随包分发。
