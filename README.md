# CCRaw

开源桌面 RAW 编辑器。支持曝光、曲线、HSL、色彩分级、局部蒙版、修复、裁切、图像合成、预设和批量导出。

## 启动

Windows，Python 3.12：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main.py
```

也可以运行 `run-source.cmd`。macOS 使用 `run-source.command` 和 `requirements-macos.txt`。

浅色为默认外观；在「查看 → 外观」切换深色。拖动滑杆、曲线或色轮实时预览。100% 视图处理可见区域的原图像素。

裁切时，框内拖动可保持尺寸平移取景，框外拖动可重画裁切框；点击「确认裁切」或按 Enter 应用。

「指令 → 生成」提供创意模板生成、风格化生成和自定义生成。模板支持分类搜索、效果预览、编辑提示词和保存自定义模板；自定义生成直接使用输入的提示词，可选择参考图。「清空」重置当前模式的模板选择和提示词。超写实壁纸先选择拍摄主体，再生成图像。

工程、选片集、预设分别保存为 `.ccraw`、`.ccrawalbum`、`.ccrawpreset`。原片保留不变。

完整离线交付目录已包含运行资源。Git 仓库和 wheel 不包含大型模型及 ExifTool。需要这些功能时，从 [运行资源](https://github.com/wamellia/CCRaw/releases/tag/runtime-assets) 下载资源包，再安装：

```powershell
python tools/fetch_assets.py --archive CCRaw-0.1.0-RuntimeAssets.zip
```

自然语言调整可连接本地或云端模型；语音识别在本地运行。资源与隐私说明见 [MODEL.md](MODEL.md)、[privacy.md](docs/privacy.md)。

「指令 → 生成」支持模板、图生图、文生图、批量队列与生成记录。在「设置」填写 Seedream 或兼容图像生成接口、模型和 API Key；生成配置与修图模型分开保存。选择参考图后勾选「发送参考图」，或选择「无参考图」。每张参考图可生成 1–4 张，队列最多 32 张。结果单独保存，可打开编辑或另存为；失败不自动重试。取消会停止等待与排队，已提交的服务商任务可能继续运行并计费。

## 构建与测试

[贡献与检查](CONTRIBUTING.md) · [模块结构](docs/architecture.md) · [构建](docs/releasing.md)

代码采用 MIT 许可。原项目、字体、模型和依赖署名保留在 [LICENSE](LICENSE)、[NOTICE](NOTICE)、[THIRD_PARTY.md](THIRD_PARTY.md) 和对应资源许可中。
