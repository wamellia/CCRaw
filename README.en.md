# CCRaw

[简体中文](README.md) | English

**An open-source photo editor and Photo Agent for Windows.**

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
| Edit | Local diagnosis of a selected photo with up to three proposals; natural language can create additional parameter proposals |
| Continue editing | Open the existing photo editor with its full toolset and save adjustments back to the Agent project |
| External editing | Review the provider, model, images to upload, prompt and cost before execution; results are saved as separate derived versions |
| Execution history | Persisted conversations, tasks and tool calls; viewing history does not execute requests again |

![CCRaw Photo Agent workspace](docs/screenshots/photo-agent.webp)

By default, Agent reuses the model configuration from the photo-editing instructions panel, so you do not need a second conversation API key. The conversation provider must support tool calling. Conversations send instructions, bounded project summaries and tool results; image analysis runs locally.

Without a configured LLM, you can still scan photos, review organization suggestions, use local search and get editing recommendations. Local semantic search and anonymous person clustering require approximately 186 MiB of checksum-verified models, available through **Local Models** (本地模型). Model files are stored separately from the source code.

Person detection and clustering provide candidate signals. The number of people in an image is unknown by default and can be verified through **Annotate People / Tags** (标注人数 / 标签). Strict person-count queries exclude photos with unknown counts. Events use EXIF capture times; missing capture times are not replaced with file modification times. For complex Chinese searches, use an Agent conversation to translate the request into search filters and an English semantic query. See the [Photo Agent guide](docs/photo-agent.md) for feature boundaries and project storage details.

## From original to finished image

### Live previews while you adjust

The image updates as you drag sliders, edit curves or move the color-grading wheels. Fit the image to the window to review the overall result, or switch to 100% to inspect original-resolution pixels in the visible area. Use the original view and before/after comparison to check your adjustments.

Image processing runs separately from interface interaction, and consecutive inputs are coalesced. A CPU supports everyday editing; DirectML, CUDA or model inference acceleration can be enabled depending on your environment. Actual responsiveness depends on image dimensions, adjustment types and hardware.

### Editing tools in one place

| Tool | What you can do |
|---|---|
| Light | Adjust exposure, contrast, shadows, highlights, black and white points, and curves |
| Color | Control white balance, HSL and color grading for overall and selective color adjustments |
| Detail | Sharpen, denoise and optionally use AI detail processing |
| Masks | Apply local adjustments with brushes, gradients and optional automatic selections |
| Crop | Adjust composition and aspect ratio; drag inside the crop rectangle to move it horizontally or vertically without changing its size |
| Retouch and effects | Apply retouching, image effects and watermarks |
| Selection and export | Use catalogs, synchronized adjustments, presets, snapshots and batch export |

After drawing a crop rectangle, drag inside it to reposition it. Drag outside it to draw a new rectangle. Click **Confirm Crop** (确认裁切) or press Enter to apply it.

**Smart Watermark** (智能水印) in the watermark panel can detect the camera brand and arrange the camera model, lens, focal length, aperture, shutter speed, ISO and capture time. Choose a brand automatically or manually; missing parameters are omitted. Previews, single-image exports and batch exports share the same watermark pipeline.

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

Light mode is the default. You can also switch to a VS Code-style dark gray interface through **View → Appearance** (查看 → 外观). Changing the theme affects the interface without changing photo adjustments or output colors.

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

The default dependencies provide a CPU runtime. For a Windows GPU environment, use `requirements-directml.txt`; for NVIDIA CUDA, use `requirements-gpu.txt`. Follow the instructions in the relevant file to remove other ONNX Runtime distributions first. Keep only one ONNX Runtime distribution installed in each environment. Driver and system requirements are documented in the dependency files and the [Windows build guide](docs/releasing.md).

## Data and open source

Photo editing runs locally. CCRaw does not automatically upload photos, logs or telemetry. Natural-language and image generation requests use the providers you configure. API keys are protected with Windows DPAPI and stored separately from the source code. See the [privacy guide](docs/privacy.md) for details.

The source code is MIT-licensed. Attribution and licenses for the upstream project, fonts, models and third-party dependencies are preserved in [LICENSE](LICENSE), [NOTICE](NOTICE), [third-party credits](THIRD_PARTY.md) and the corresponding resource license files.

[Development and checks](CONTRIBUTING.md) · [Architecture](docs/architecture.md) · [Windows builds](docs/releasing.md)
