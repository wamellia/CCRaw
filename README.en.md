# CCRaw-Pro v1.2

[简体中文](README.md) | English

**An open-source professional photo editor and Photo Agent for Windows.**

v1.2 improves editing efficiency, template watermarks and automatic GPU acceleration. Professional tools are grouped into Light, Color, Detail, Composition, Local and Effects; settings use a two-level directory. Template watermarks provide visual layout controls with standard2 as the default. Automatic acceleration selects a compute path according to image size and cached results, and displays GPU information. The release is named **CCRaw-Pro v1.2**, with software version **1.2.0**. Floating-point wide-gamut processing, culling and catalogs, Photo Agent and image generation remain available.

CCRaw brings photo adjustments, local editing, photo selection and AI image creation into one workspace. Import an original image, adjust light and color with live previews, compare results, save your project and export a separate finished image. Originals stay intact. Adjustments can be undone or saved as presets for other photos.

![CCRaw editor in dark mode](docs/screenshots/editor-dark.webp)

## Two workspaces

The welcome screen offers **New Photo Agent Project** (Photo Agent 新建工程), **Open Agent Project** (打开 Agent 工程) and **Quick Edit** (快捷编辑) on the left. Recent Agent projects appear on the right, sorted by when they were last opened. Search by name or path and click a project to resume work. Quick Edit provides the existing RAW adjustments, catalogs, live previews, masks, cropping, instructions and image generation workflows. Photo Agent manages a collection of photos as a project, combining local analysis with model conversations to organize photos, find images and choose editing proposals.

![CCRaw workspace launcher](docs/screenshots/workspaces.webp)

An **Appearance** (外观) control is available at the bottom left of the welcome screen, at the top of Photo Agent and in the photo editor toolbar. Choose light or dark mode; the change applies to all open windows and your preference is saved.

### Photo Agent

Create a `.ccrawagent` project and import photos or folders to begin scanning. The project references your originals. Analysis results, conversations and derived versions are stored in the adjacent `.ccrawagent-data` folder.

| Task | Result |
|---|---|
| Organize | Exact duplicate groups based on SHA-256, similar-image candidates, exposure and sharpness hints, events grouped by capture time, and anonymous person clusters |
| Find photos | Constrained queries from natural language, combining capture dates, filenames, tags, local CLIP vectors, person clusters and quality signals |
| Edit | Local diagnosis with up to three proposals per selected photo; natural-language color adjustments, cropping, rotation, curves and geometric masks, with before/after previews before applying |
| Adjust multiple photos | Create proposals for 1–30 selected photos while preserving each photo's untouched settings; preview and approve them individually |
| Continue editing | Open the existing photo editor with its full toolset and save adjustments back to the Agent project |
| External editing | Review the provider, model, images to upload, prompt and cost before execution; results are saved as separate derived versions |
| Execution history | Persisted conversations, tasks and tool calls; viewing history does not execute requests again |

![CCRaw Photo Agent workspace](docs/screenshots/photo-agent.webp)

By default, Agent reuses the model configuration from the photo-editing instructions panel, so you do not need a second conversation API key. The conversation provider must support tool calling. Conversations send instructions, bounded project summaries and tool results; image analysis runs locally.

Without a configured LLM, you can still scan photos, review organization suggestions, use local search and get editing recommendations. Local semantic search and anonymous person clustering require approximately 186 MiB of checksum-verified models, available through **Settings → AI and services → Local models** (设置 → AI 与服务 → 本地模型). Model files are stored separately from the source code.

Person detection and clustering provide candidate signals. The number of people in an image is unknown by default and can be verified through **Annotate People / Tags** (标注人数 / 标签). Strict person-count queries exclude photos with unknown counts. Events use EXIF capture times; missing capture times are not replaced with file modification times. For complex Chinese searches, use an Agent conversation to translate the request into search filters and an English semantic query. See the [Photo Agent guide](docs/photo-agent.md) for feature boundaries and project storage details.

Select a proposal and click **Preview Proposal** (预览方案) to compare the current adjustments with the proposed result. Previewing does not create a derived version. Try “Set exposure to 0.3 stops for all selected photos while keeping their individual crops” or “Crop the selected photo to its right half and show a preview first.” Parameter names, ranges and coordinates are validated; unsupported requests produce an error.

![CCRaw Agent before/after proposal preview](docs/screenshots/agent-preview.webp)

External tool-calling agents can connect over standard stdio MCP to the same photo tools, discover controls, search the project, create proposals and receive before/after preview images. Start MCP from Python source and explicitly authorize editing targets by photo ID. Derived versions still require approval in the app. See [Agent tools and MCP](docs/agent-tools.md#english-quick-start) for setup and examples.

## From original to finished image

### Live previews while you adjust

The image updates as you drag sliders, edit curves or move the color-grading wheels. Fit the image to the window to review the overall result, or switch to 100% to inspect original-resolution pixels in the visible area. Use the original view and before/after comparison to check your adjustments.

Image processing runs separately from interface interaction, and consecutive inputs are coalesced. Automatic acceleration selects a compute path according to image size, active tools and cached results. It supports OpenCL neighbourhood filters and optional DirectML or CUDA pixel processing and model inference. The editor shows the GPU model, dedicated memory and the actual processing path. Responsiveness depends on image dimensions, adjustment types and hardware.

Professional controls and composite masks preview changes as you make them. Confirm a composite-mask edit to keep it as one undo step, or cancel to restore the previous state. Preview requests are scheduled at up to 60 Hz without reducing the working preview resolution during a drag; the 100% view updates visible original pixels. Actual image refresh rates depend on processing time. Decoded photos, curve interpolation, filter inputs and color stages reuse bounded caches to avoid repeated work.

### Editing tools in one place

The editor uses six categories: **Tone, Color, Detail, Composition, Local and Effects**. Professional tools sit beside their related editing operations in collapsible sections. Color includes white balance, grading, black-and-white mixing, point color, calibration and profiles; Composition includes cropping, optics and perspective; Local includes masks, healing and red-eye correction. Reset the current section directly, or use **Photo → Adjustments → Reset all adjustments** for the whole photo. Both support undo.

| Tool | What you can do |
|---|---|
| Light | Adjust exposure, contrast, shadows, highlights, black and white points, and curves |
| Color | Control white balance, HSL and color grading for overall and selective color adjustments |
| Detail | Sharpen, denoise and optionally use AI detail processing |
| Composition | Crop and rotate, correct lens distortion and perspective; move a crop rectangle without changing its size |
| Local | Masks, brushes, gradients, automatic selections, healing and red-eye correction |
| Effects | Vignetting, film grain, template watermarks and margins |

After drawing a crop rectangle, drag inside it to reposition it. Drag outside it to draw a new rectangle. Click **Confirm Crop** (确认裁切) or press Enter to apply it.

Watermarks use semi-utils templates and composable processors. The default, `standard2`, combines rounded corners, shadow, spacing and capture parameters. Choose from seven current templates and legacy `Layout.Type` layouts, then adjust text, fonts, colors, logos, margins, alignment, blur and shadows through visual controls. Template watermarks replace the former native plain borders; older project settings migrate when opened.

Preview, single-image export and batch export share the same processing pipeline. Layout uses the final photo dimensions before scaling for display, while photograph data retains floating-point precision. See [watermark templates](docs/watermark.md) for details.

### Save adjustments and reuse a look

Editing projects use `.ccraw`, catalogs use `.ccrawalbum`, and presets use `.ccrawpreset`. Keep different editing proposals as snapshots, then use presets and synchronized adjustments to give a group of photos a consistent look.

## Edit and create with text

The **Instructions** (指令) panel provides photo editing and image generation workflows. Natural-language editing can connect to a local model or cloud provider and turn descriptions into parameters you can continue editing. An optional SenseVoice model supports local speech recognition.

### Three ways to generate images

| Mode | How it works |
|---|---|
| Creative templates | Start from 50 built-in themes to create stickers, photo booth strips, miniature scenes, comics, blueprints and more |
| Stylized generation | Choose from 31 styles spanning photography, anime, painting, pixel art and artist-inspired looks |
| Custom generation | Write your own prompt and configure reference images and generation parameters |

![CCRaw image generation panel](docs/screenshots/generation.webp)

Templates support categories, search and visual previews. Select a theme, refine its prompt or save your own template. **Clear** (清空) resets the template selection and prompt for the current mode. The hyperrealistic wallpaper template asks you to choose a subject first.

![CCRaw style browser](docs/screenshots/templates.webp)

![CCRaw creative template browser](docs/screenshots/creative-templates.webp)

Theme images illustrate a style direction. Actual results depend on your reference image, prompt and configured model.

### Reference images, queues and generation history

Generation supports text-to-image and image-to-image requests. Use the current photo as a reference, or generate from a prompt alone. Each reference image can produce 1–4 results, with up to 32 images in a queue. Results are saved separately and can be opened for further editing or saved elsewhere. Generation history remains available after restarting the app.

In the generation panel's **Settings** (设置), enter the endpoint, model and API key for Seedream or a compatible image generation provider. Generation settings are stored separately from the editing model configuration. A processed reference image is sent only when **Send Reference Image** (发送参考图) is enabled. Basic photo editing does not require a cloud service. Providers may charge per request, and cancelling an already submitted request may not stop processing on the server.

## Choose your appearance

Light mode is the default. Appearance and Settings are available in the launcher, Photo Agent and editor. Choose light, VS Code-style dark gray, or the Windows system theme, and switch between English and Chinese. Ctrl+, opens shared settings. Themes do not change photo adjustments or output colors.

## Professional editing and catalog management

The Professional page adds optical correction, perspective and flips, RGB calibration, detailed sharpening and noise reduction, an eight-color monochrome mixer, point color, LUT/ICC/DCP profiles, and red-eye correction. Point color has a selection overlay; monochrome supports an automatic starting mix and on-image dragging. Masks support add/subtract/intersect components, and synchronization transfers selected parameter groups.

The wide-gamut renderer uses floating-point Rec.2020, preserving signed and above-white intermediate samples. Export supports sRGB, Adobe RGB, Display P3, ProPhoto RGB and Rec.2020 with embedded ICC, plus 16-bit TIFF/PNG and flattened RGB PSD. Monitor ICC, soft proofing and gamut warnings affect presentation only. Export presets, metadata policies, sharpening, external-editor copies and XMP complete the output workflow. Support boundaries are described in the [editing guide](docs/editing.md).

Photo Agent and Quick Edit's Manage Albums share ratings, flags, color labels, albums and folders, smart albums, saved filters, comparison and survey views, stacks and virtual copies. Batch metadata, anonymous face-cluster corrections, relinking, offline proxies and project trash are available through the management view. See the [catalog guide](docs/catalog.md).

Shared settings cover performance, cache cleanup, import defaults, existing provider configurations and autosave recovery. Shareable settings omit credentials; originals remain unchanged. See the [settings guide](docs/settings.md). Detailed guides are currently in Chinese; application labels can be switched to English.

![CCRaw editor in light mode](docs/screenshots/editor-light.webp)

The screenshots above show the application running. Interface labels in the screenshots are in Chinese.

## Get started

Current and future CCRaw releases target **64-bit Windows**. **64-bit Python 3.12** is recommended for running from source.

Open PowerShell in the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

After installing dependencies, you can also launch with `run-source.cmd`. If your development environment is already configured, run the launch command directly without creating the environment again.

For Photo Agent conversations and semantic models, install the optional dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-agent.txt
```

1. Choose **Quick Edit** (快捷编辑) and click **Open** (打开) to import a RAW or common image file, or create a Photo Agent project and import photos.
2. Select light, color, mask, crop and other tools on the right and preview your adjustments live.
3. Save the project or click **Export** (导出) to create a finished image.
4. For text-based editing or image generation, open **Instructions** (指令) on the left and configure the relevant provider.

### Optional runtime resources

The Git repository and Python packages include the interface, font and theme previews. Large AI models and ExifTool are provided in a separate resource bundle. You can begin with basic editing and install the resources when you need the associated features.

Download `CCRaw-0.1.0-RuntimeAssets.zip` from the [runtime resource release](https://github.com/wamellia/CCRaw/releases/tag/runtime-assets), then run this command in the project directory:

```powershell
.\.venv\Scripts\python.exe tools/fetch_assets.py --archive "C:\path\CCRaw-0.1.0-RuntimeAssets.zip"
```

Replace the example path with the actual download location. If your complete offline distribution already contains these resources, you do not need to install them again. See the [model resource guide](MODEL.md) for model purposes and licenses.

### Optional GPU acceleration

The default dependencies provide a CPU runtime. When the driver and OpenCV support OpenCL, automatic mode can also accelerate suitable neighbourhood filters without installing CUDA packages. Clarity, texture and sharpening can run as a combined operation to reduce device transfers. Small images and cached results retain the CPU fast path. Selecting CPU manually disables automatic GPU routing for photo processing. Device and execution details are available under Settings → Performance & storage → Compute.

For additional ONNX acceleration, use `requirements-directml.txt` on Windows or `requirements-gpu.txt` for NVIDIA CUDA. Follow the instructions in the relevant file to remove other ONNX Runtime distributions first. Keep only one ONNX Runtime distribution installed in each environment. Driver and system requirements are documented in the dependency files and the [Windows build guide](docs/releasing.md).

## Data and open source

Photo editing runs locally. CCRaw does not automatically upload photos, logs or telemetry. Natural-language and image generation requests use the providers you configure. API keys are protected with Windows DPAPI and stored separately from the source code. See the [privacy guide](docs/privacy.md) for details.

The source code is MIT-licensed. Attribution and licenses for the upstream project, fonts, models and third-party dependencies are preserved in [LICENSE](LICENSE), [NOTICE](NOTICE), [third-party credits](THIRD_PARTY.md) and the corresponding resource license files.

[Development and checks](CONTRIBUTING.md) · [Architecture](docs/architecture.md) · [Windows builds](docs/releasing.md)
