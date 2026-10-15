# CCRaw-Pro v1.2

[简体中文](README.md) | English

**Cull, edit, organize and create with AI in one Windows photo workspace.**

CCRaw is an open-source desktop application that brings together a RAW editor, a photo catalog and Photo Agent. Open a single image for detailed work, or build a project around a shoot: organize and select your photographs, adjust and compare them, keep alternative treatments, then export a consistent set.

Original files stay in their own locations. Edits are stored as reversible parameters. Sliders, curves and color-grading controls support live previews; when you prefer a conversation, Agent uses the same Photo Engine to prepare inspectable proposals. Manual editing and natural-language instructions can continue the same work.

**Windows x64 · Chinese / English · Python 3.12 recommended · MIT source license**

[Download v1.2](https://github.com/wamellia/CCRaw/releases/tag/v1.2) · [Install and run](#install-and-run) · [Product overview](#product-overview) · [Documentation](#documentation-and-contributing)

![CCRaw photo editor in deep-gray mode](docs/screenshots/editor-dark.webp)

*The image, filmstrip and six editing categories share one workspace. Images in this README are application screenshots; the selected theme, tools and service configuration affect what you see.*

## Product overview

CCRaw connects the steps between importing photographs and delivering finished images:

| What you want to do | How CCRaw helps |
|---|---|
| Choose the photographs worth editing | Ratings, pick/reject flags, color labels, two-image comparison, survey views and burst stacks |
| Refine an individual image | RAW development, curves, HSL, color grading, point color, local masks, optics and perspective correction |
| Give a set a consistent look | Presets, snapshots, virtual copies, selective adjustment synchronization and batch export |
| Organize a growing local collection | Albums, folders, smart albums, saved filters, metadata, offline proxies and missing-original relinking |
| Find an image by description or prepare an edit | Local analysis, hybrid retrieval, model conversations and before/after proposals in Photo Agent |
| Prepare a consistent presentation | Template watermarks with capture information, brand logos, type and spacing controls |
| Develop a creative variation | Creative templates, style presets or custom generation prompts, with independently saved results |

Use the editor and catalog on their own, or add local models and external AI services when needed. Basic adjustments, culling and export do not require a cloud account.

[Editing](#professional-photo-editing) · [Catalogs](#culling-and-catalog-management) · [Photo Agent](#photo-agent) · [Watermarks](#template-watermarks-and-delivery) · [AI creation](#ai-image-creation) · [FAQ](#frequently-asked-questions)

### Where it fits

- **After a shoot:** import, compare nearby frames, mark your selections, finish the color treatment and apply consistent watermark and export presets.
- **For a detailed edit:** start with exposure and white balance, refine curves and individual colors, use local masks and inspect details at 100%.
- **For an ongoing photo collection:** organize with albums, keywords and rules, then search for an image whose subject you remember better than its filename.
- **For alternative treatments:** preserve different looks through snapshots and virtual copies, or continue from a finished photograph into illustration, film, miniature scenes and other generated variations.

## Two workspaces

Choose an entry point for the size of the task. Both workspaces share the photo-processing tools, and adjustments can move between Agent and the full editor.

| Entry | Best for | How it works |
|---|---|---|
| **Quick Edit** | One photograph or a filmstrip of selections | Open originals in the full editor; save individual editing projects and catalogs as needed |
| **New Photo Agent Project** | A shoot or a themed collection | Import and analyze photos, then organize, search and choose proposals through the gallery and conversation |
| **Open Agent Project** | Resuming existing work | Restore photographs, adjustments, suggestions, conversations and execution history |

Recent Agent projects appear on the right of the welcome screen, ordered by when they were last opened. Search by name or path and click to resume. The welcome screen, Agent and editor all provide appearance and settings controls.

<details>
<summary>See the project launcher</summary>

![CCRaw project launcher and recent projects](docs/screenshots/workspaces.webp)

</details>

## Professional photo editing

### See each adjustment as you make it

Move a slider, a curve point, a color-grading control or a mask parameter and the preview updates with the change. Fit view helps judge composition and overall color; the 100% view inspects visible original-resolution pixels. Use Original and Before/After to assess your treatment.

Consecutive inputs are coalesced so that the latest parameters take priority. Decoded images, curves and filter results are reused where possible. Preview requests are scheduled at up to 60 Hz while retaining the working preview resolution; actual refresh rates depend on image dimensions, active tools and hardware. This does not promise a fixed 60 fps for every original and operation.

![Before/after exposure and tonal adjustments on the landscape sample](docs/screenshots/editor-light.webp)

*Example: adjust exposure, shadows and highlights in `DSC_0601.NEF`, then compare the original and current treatment in the light interface.*

### Six categories organized around the editing task

| Category | Main controls | Typical use |
|---|---|---|
| **Light** | Exposure, contrast, shadows, highlights, black/white points, exposure curves, RGB and channel curves | Establish tonal relationships and control contrast and channel tone |
| **Color** | White balance, HSL, three-way grading, eight-band monochrome mixing, point color, RGB calibration, LUT/ICC/DCP profiles | Correct casts, target individual colors and build color or monochrome treatments |
| **Detail** | Clarity, texture, standard and advanced sharpening, luminance/color noise reduction, optional AI detail tools | Balance fine detail, noise and texture |
| **Composition** | Crop, rotation, straightening, distortion, optical vignetting, lateral chromatic aberration, perspective, scale and flips | Refine framing and correct geometric problems |
| **Local** | Geometric and brush masks, color/luminance ranges, combined masks, optional automatic selections, healing, cloning and red-eye correction | Adjust selected areas and address local distractions |
| **Effects** | Vignetting, film grain, template watermarks and margins | Finish the look and prepare the presentation |

Related parameters sit in collapsible sections. Reset the current section directly, or use the menu beside the reset button to restore all adjustments for the photo. Both operations support undo.

- **Reposition a crop:** drag inside an existing rectangle to move it without changing its size. Click Confirm Crop or press Enter to apply it.
- **Sample a color:** point color supports up to 16 original-image samples, each with hue, saturation, luminance, range and feathering controls and a selection display.
- **Adjust monochrome from the image:** start with automatic mixing or drag the corresponding color band directly in the photograph.
- **Combine masks:** add, subtract or intersect geometric, brush, color, luminance and AI masks; change order, inversion and opacity. Confirming the combination dialog creates one undoable change; cancelling restores the previous state.

<details>
<summary>See the workflow: curves, color grading and HSL</summary>

**City at dusk · RGB curves**

![RGB curve adjustments on the city NEF sample](docs/screenshots/workflow-curves.webp)

**The same city photograph · shadow and highlight grading**

![Color grading on the same city sample](docs/screenshots/workflow-grading.webp)

**Flowers · HSL controls**

![HSL adjustments on the flower sample](docs/screenshots/workflow-color.webp)

</details>

<details>
<summary>See the workflow: cropping, local masks and original-pixel detail</summary>

**Swan · refine the crop**

![Positioning a crop rectangle on the swan photograph](docs/screenshots/workflow-crop.webp)

**Rainbow · a radial mask and local adjustment**

![A radial mask on the rainbow sample](docs/screenshots/workflow-local-mask.webp)

**Seagull · fit-view texture and sharpening**

![Detail controls on the seagull NEF sample](docs/screenshots/workflow-detail.webp)

**The same seagull · 100% original-pixel inspection**

![Original-pixel seagull detail with the advanced sharpening panel](docs/screenshots/workflow-detail-100.webp)

</details>

### Manage development, display and output separately

Compatibility development uses sRGB for existing treatments. Wide-gamut development uses floating-point Rec.2020 and retains negative and above-white intermediate values. Supported 16-bit TIFF, PNG and PSD inputs are not reduced to 8-bit before processing. RAW support still depends on camera, encoding and LibRaw decoding capabilities.

Monitor ICC, soft proofing and output color space are configured separately. Export to **sRGB, Adobe RGB, Display P3, ProPhoto RGB or Rec.2020**. Soft proofing includes output ICC, rendering intent, black-point compensation, paper/ink simulation and gamut warnings. Screen previews remain subject to the monitor and SDR display range.

Import local Lensfun XML for lens calibration, and `.cube`, RGB ICC or DCP files for profiles. Supported fields are disclosed; different editors and proprietary extensions do not necessarily produce identical appearances. See [professional editing](docs/user/editing.md) for details.

### Combine multiple frames

Select **2–32 photographs** in the filmstrip and open Photo → Merge, or use the context menu:

| Method | Purpose |
|---|---|
| **Focus stacking** | Combine sharp regions from different focus distances for static scenes such as macro subjects |
| **HDR stack** | Blend different exposures with alignment and deghosting options; uses exposure fusion rather than an HDR radiance map |
| **Panorama** | Stitch overlapping photographs with recognizable detail into a larger frame |

Choose the reference image, whether to use current adjustments, automatic alignment and edge cropping. Preview first, then process original-resolution images. The result is saved as a separate **16-bit linear DNG** and added to the filmstrip. Significant parallax, movement or insufficient overlap affects the result.

### Save an edit and reuse a look

Individual editing projects use `.ccraw`, catalogs use `.ccrawalbum`, and presets use `.ccrawpreset`. Snapshots preserve stages of an edit; virtual copies let you develop independent treatments from one original.

Synchronize Adjustments lets you select parameter groups. By default it preserves the target photo's camera white-balance basis, crop, geometry, masks, retouching and watermark; include those groups explicitly when needed. Each photograph retains its own recipe and history.

## Culling and catalog management

Photo Agent includes catalog management directly. Quick Edit opens the same tools through Manage Catalog below the image. Ratings, organization state and editing parameters live in the project so you can move between selection and editing.

![The six sample photographs with albums, ratings and color labels](docs/screenshots/workflow-catalog.webp)

*The catalog demonstration uses JPEG copies exported from the same photographs through Photo Engine, showing mixed aspect ratios, ratings, pick flags and albums.*

### Keep the frames worth developing

Use **0–5 stars, pick/reject/unflagged states and color labels** for a first selection. Browse with the arrow keys; enable Auto Advance to move on after rating. Shortcuts include `0–5` and `P/X/U`; focused text fields continue to receive normal typing.

Two-image comparison supports synchronized zooming and panning, with original-pixel regions available for detail checks. Survey views help narrow a candidate set. Management history supports undo and redo, while AI quality hints remain separate from your ratings.

<details>
<summary>See landscape and portrait photographs in comparison view</summary>

![Landscape and rainbow samples compared at their own aspect ratios](docs/screenshots/workflow-catalog-compare.webp)

</details>

### Organize a collection your way

| Feature | How you use it |
|---|---|
| Albums and folders | Nested folders, a target album and quick collections; one image can belong to multiple albums |
| Smart albums | Dynamically collect photos by rating, flag, date, camera, lens, ISO, format, keywords and editing state |
| Saved filters | Name and reuse common conditions, update them or turn them into a smart album |
| Stacks | Group manually or by capture time, set a cover, collapse/expand and review burst quality suggestions |
| Virtual copies | Share the original while keeping independent recipes and history |
| Photo information | Edit title, description, creator, copyright, location and hierarchical keywords; batch changes apply only to checked fields |
| Person clusters | Merge, split and correct anonymous person candidates |

When originals are offline, fingerprinted **16-bit TIFF proxies** allow continued editing. Resume original-based export after relinking. Missing files can be relocated individually or by folder. The project trash manages removal records without deleting disk originals by default; proxies, caches and originals are managed separately.

Actions reflect the selection count and task state, and context menus use the same actions. See [culling and catalog management](docs/user/catalog.md) for advanced filters, sorting, proxies and trash operations.

## Photo Agent

**Treat a photo collection as a project, organize work through conversation and finish through inspectable proposals.**

Agent shares the full editor's Photo Engine. It uses local analysis of imported photographs and translates descriptions into constrained tool calls. Continue any detailed treatment in the full editor and save adjustments back to the Agent project.

![CCRaw Photo Agent gallery and conversation workspace](docs/screenshots/photo-agent.webp)

*The example project contains six demonstration copies and proposals from real local analysis and recommendations. The conversation panel shows its unconfigured model state.*

### Scan, suggest, search and edit

| Stage | What it provides |
|---|---|
| **Local scanning** | File fingerprints, metadata, thumbnails, exposure and sharpness signals; semantic and face features when models are installed |
| **Organization suggestions** | Exact duplicates, similar-image candidates, quality hints, capture-time events and anonymous person clusters |
| **Natural-language search** | Combined date, tag and camera metadata with local semantic vectors, people and quality conditions |
| **Editing recommendations** | Local diagnosis of selected photos, with up to three proposals per image and further natural-language adjustments |
| **Proposal preview** | Compare current and proposed recipes; load original-pixel comparison within the application |
| **Approved execution** | Render a separate full-resolution 16-bit TIFF from the original and record provenance and history |

Start with instructions such as these, using dates, subjects and selections that exist in your project:

> Organize this project and list duplicate photos and candidates with low sharpness.
>
> Find sunset photographs taken by the sea last summer.
>
> Set exposure to 0.3 stops for the selected photos, preserve each crop and show a preview first.
>
> Recommend a natural enhancement for the current photo, then make the color a little warmer.

Batch proposals cover **1–30 selected photographs**. Each starts from its own recipe, preserving untouched parameters. Recommending again replaces that photo's pending local proposals. The current selection determines the proposal list; clear pending options without removing executed versions or history.

![CCRaw Agent before/after proposal comparison](docs/screenshots/agent-preview.webp)

### Add models when needed

Agent reuses the conversation configuration from the editing Instructions panel. Use a local service such as Ollama or LM Studio, or a configured cloud interface; the chosen model must support tool calling. Semantic search and person clustering use separate local **CLIP, YuNet and SFace** models, approximately **186 MiB**, installed through Settings → AI and services → Local models with checksum verification.

Without an LLM you can still scan, review suggestions, search filenames/tags/metadata and use local editing recommendations. Local semantic retrieval becomes available with its model. Complex Chinese descriptions and combined conditions work best when an Agent conversation translates them into a query.

<details>
<summary>See local semantic search: pink flowers</summary>

![Local semantic retrieval for pink flowers returns the flower sample](docs/screenshots/workflow-search.webp)

*A cropped interface capture: the installed local semantic model returns the flower photograph for the query `pink flowers`.*

</details>

Person clusters are anonymous candidates requiring review. People counts are unknown by default; strict count conditions use verified annotations. Similarity, quality and event suggestions are selection aids and do not automatically delete originals. See [Photo Agent](docs/user/photo-agent.md) for operating boundaries.

### External editing and other agents

Before external generation or editing, the application shows the provider, model, upload contents, prompt and cost; unknown cost is labeled as unknown. Conversation tools prepare a proposal, while the interface handles approval and execution. Results are saved as independent derived versions.

External tool-calling agents can use **standard stdio MCP** to access the same **17 photo tools** for parameter discovery, project search, catalog management, proposals and previews. Authorization specifies the project and photo IDs; application and paid execution remain interface approvals. See [Agent tools and MCP](docs/user/agent-tools.md).

## Template watermarks and delivery

### Arrange the photograph, capture information and branding together

Watermarks use templates and composable image processors. The default **`standard2`** combines capture information, rounded corners, shadow and spacing. Visual controls expose `Layout.Type` and processing steps for a consistent presentation across a set.

| Template | Layout direction |
|---|---|
| `standard1` / `standard2` | Standard capture parameters with camera, lens, time and brand marks |
| `nikon_blur` / `blur` | Blurred backgrounds and capture information |
| `normal1` / `normal2` | Parameters in the lower-right corner, or folder name and time |
| `center_logo` | A centered logo with surrounding text |
| Legacy `Layout.Type` layouts | Left/right logos, black/red layouts, corner text, square fill, minimal and border/blur combinations |

Adjust text, fonts, sizes, colors, logos, margins, spacing, alignment, target aspect ratio, blur, corners and shadows. Add, remove or reorder processing steps. Detect a brand automatically or select it manually, and import PNG logos. Actual and 35mm-equivalent focal lengths remain separate.

![Configuring and previewing the standard2 watermark on the swan photo](docs/screenshots/workflow-watermark.webp)

*The swan photograph supplies its Nikon camera, lens, focal length, shutter and ISO information for the rounded, shadowed `standard2` layout.*

<details>
<summary>See standard1 and the crop result</summary>

**standard1 template**

![The same swan photograph in the standard1 layout](docs/screenshots/workflow-watermark-standard1.webp)

**Confirm the crop and continue adjusting in the editor**

![The cropped swan photograph in the editor](docs/screenshots/workflow-crop-result.webp)

</details>

Preview, single/batch export and external-editor handoff share the configuration. Layout uses the final cropped and rotated photo dimensions. Large-image screen previews use scaled grids and supersampling, so filtered previews approximate full-resolution exports; complete export processes the original dimensions. Template layouts replace the former native plain borders, and older settings migrate when opened. See [watermark templates](docs/user/watermark.md).

### Deliver a set under one output policy

Export presets retain naming, filename conflicts, resolution, dimensions, bit depth, color space, output sharpening and metadata policy. Remove GPS or keep copyright-only metadata in separate output files.

| Output or exchange | Purpose |
|---|---|
| JPEG | Everyday presentation and sharing, with 8-bit output |
| TIFF / PNG | 8/16-bit delivery and further processing |
| Merged RGB PSD | Exchange image pixels with compatible applications; no layers, smart objects or PSB |
| External-editor copy | Hand off a separate TIFF/PSD to a configured editor and import its return as a new version |
| XMP | Exchange standard metadata and supported editing fields while retaining the complete CCRaw recipe |

![Configuring 16-bit TIFF, Display P3, naming and GPS removal](docs/screenshots/workflow-export.webp)

*Example delivery settings: 16-bit TIFF, Display P3, a filename template and removal of GPS/location fields.*

Fields and algorithms differ between editors, so XMP exchange does not guarantee pixel-identical results in every application.

## AI image creation

Generation in the Instructions panel supports text-to-image and image-to-image work. Reference the current photograph or use only text. Results are saved separately and can be opened for further editing.

![CCRaw image-generation panel](docs/screenshots/generation.webp)

*Example: select the current swan photograph as the reference and prepare a watercolor generation task.*

| Mode | Starting point | Example directions |
|---|---|---|
| **Creative templates** | 50 built-in themes | Stickers, photo booth strips, Y2K, 3D heads, felt miniatures, clay animation, comics and blueprint posters |
| **Stylized generation** | 31 styles | Portrait, cinematic, Chinese-inspired, anime, ink, oil, watercolor, pixel and artist-inspired treatments |
| **Custom generation** | Your own prompt | Define subject, scene, materials, framing and lighting, with your reference and generation settings |

Browse by category, search and inspect theme previews. Prompts remain editable, personal templates can be saved, and Clear resets the selection and prompt for the current mode. Theme images illustrate a direction; actual results depend on references, prompts and the configured model.

<details>
<summary>See the creative and style browsers</summary>

![CCRaw creative-template browser](docs/screenshots/creative-templates.webp)

![CCRaw style browser](docs/screenshots/templates.webp)

</details>

Generate **1–4 results per reference**, with up to **32 queued images**. Task and result records remain available after restarting; interrupted requests are not automatically resubmitted. An optional SenseVoice model transcribes speech locally before passing text into the editing workflow.

Generation uses a separately configured Seedream or compatible service rather than the conversation configuration. An edited reference is sent only when Send Reference Image is selected. Cancelling an already submitted request may still incur provider charges.

## Everyday experience and automatic acceleration

Choose light, VS Code-style deep gray or the Windows system appearance across workspaces. Chinese/English language and application settings synchronize to open windows. Changing the theme changes the UI rather than photo parameters or output colors.

Settings use a two-level directory for Workspace, Photos and color, Performance and storage, AI and services, Workflow, and Save and recovery. Application defaults and project overrides are separate, with dedicated entries for background tasks, caches, autosave, backups and recovery. Open settings with `Ctrl+,`.

Automatic acceleration selects a route according to image dimensions, tools and cached results. Suitable clarity, texture and sharpening operations can run as a combined OpenCL chain; large-radius single-channel Gaussian filters may also use the GPU. Small images and cached results retain the CPU fast path. DirectML/CUDA pixel processing and model inference depend on the installed runtime.

The editor shows the GPU model, dedicated memory and current route. Settings → Performance and storage → Compute shows drivers, available backends and actual execution steps. Detecting a GPU does not mean every operation runs on it. Manual CPU mode provides a selectable photo-processing path for troubleshooting. See [system settings](docs/user/settings.md).

<details>
<summary>See compute-device and GPU information</summary>

![GPU model, memory, driver and execution information in Compute settings](docs/screenshots/workflow-settings.webp)

</details>

## Install and run

### Runtime requirements

| Item | Current support |
|---|---|
| Desktop OS | 64-bit Windows; current and future desktop releases focus on Windows |
| Python | 64-bit 3.12 recommended; package metadata allows 3.12–3.13 |
| Basic editing | CPU execution, with automatic acceleration according to driver/runtime support |
| Agent conversations | Optional Agent dependencies and a tool-calling model service |
| Semantic search and person clustering | Separately installed local models |
| AI generation | A separately configured compatible service and credentials |

### Option 1: Run from source

Download the source, or clone with Git:

```powershell
git clone https://github.com/wamellia/CCRaw.git
cd CCRaw
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements/base.txt
.\.venv\Scripts\python.exe main.py
```

With an existing development environment, run only the final launch command in the project directory, or double-click `run-source.cmd`.

For Agent conversations or MCP, add the optional dependencies in the same environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements/agent.txt
```

### Option 2: Install the Python release package

The [v1.2 Release](https://github.com/wamellia/CCRaw/releases/tag/v1.2) provides `ccraw-1.2.0-py3-none-win_amd64.whl`, a source archive and checksums. Replace the example path with the downloaded wheel location:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install "C:\Downloads\ccraw-1.2.0-py3-none-win_amd64.whl"
.\.venv\Scripts\ccraw.exe
```

The installed `ccraw.exe` is an entry point inside the virtual environment and still requires that Python environment. This release's new features are supplied through source and Python packages; consult the corresponding release notes when downloading portable applications from other releases.

Wheel users can enable optional Agent dependencies when installing:

```powershell
.\.venv\Scripts\python.exe -m pip install "C:\Downloads\ccraw-1.2.0-py3-none-win_amd64.whl[agent]"
```

### Add models and runtime resources as needed

| Capability | Configuration |
|---|---|
| Agent conversations and natural-language editing | Settings → AI and services → Conversation and editing; configure a local/cloud service and model |
| Local semantic retrieval and person clustering | Settings → AI and services → Local models; download verified models |
| Optional AI detail, automatic selections, speech and complete metadata tooling | Install the corresponding runtime resources and enable available models |
| Image generation | Generation settings; enter the endpoint, model and API key |

Large AI models and ExifTool are supplied through a separate [runtime resource bundle](https://github.com/wamellia/CCRaw/releases/tag/runtime-assets). Import a downloaded bundle from the source directory:

```powershell
.\.venv\Scripts\python.exe tools/assets/fetch_assets.py --archive "C:\Downloads\CCRaw-0.1.0-RuntimeAssets.zip"
```

Complete offline distributions may already contain these resources. Agent models and editor runtime assets are managed separately. See [model resources](docs/user/models.md) for purposes and licenses.

### Optional GPU environments

The default installation provides CPU execution. Suitable filters can use OpenCL when supported by the driver and OpenCV. Additional Windows GPU environments are described in `requirements/windows-directml.txt`; NVIDIA CUDA environments use `requirements/windows-cuda.txt`.

Those files explain runtime requirements and ONNX Runtime replacement. Remove conflicting ONNX Runtime distributions first and keep only one in each environment. See the [Windows build and runtime guide](docs/development/releasing.md).

## Follow the sample-photo workflow

The new examples use five NEF photographs and a swan image to demonstrate operations suited to each scene. Open a screenshot directly from the table:

| Sample | Demonstration |
|---|---|
| `DSC_0601.NEF` · landscape | [Tonal before/after](docs/screenshots/editor-light.webp), [Agent local proposal preview](docs/screenshots/agent-preview.webp) |
| `DSC_1107.NEF` · city at dusk | [RGB curves](docs/screenshots/workflow-curves.webp), [color grading](docs/screenshots/workflow-grading.webp) |
| `DSC_2180.NEF` · rainbow | [Radial mask and local adjustment](docs/screenshots/workflow-local-mask.webp) |
| `DSC_7099.NEF` · flowers | [HSL controls](docs/screenshots/workflow-color.webp), [local semantic retrieval](docs/screenshots/workflow-search.webp) |
| `Z30_5700.NEF` · seagull | [Detail controls](docs/screenshots/workflow-detail.webp), [100% original-pixel inspection](docs/screenshots/workflow-detail-100.webp) |
| Swan photograph | [Crop framing](docs/screenshots/workflow-crop.webp), [template watermark](docs/screenshots/workflow-watermark.webp), [crop result](docs/screenshots/workflow-crop-result.webp) |

Catalog screenshots use JPEG demonstration copies exported from the same photographs through Photo Engine. The repository contains interface captures; original NEFs, standalone demonstration JPEGs, projects and caches remain local. See [screenshot and sample notes](docs/screenshots/README.md).

## Your first project

1. **Choose a workspace.** Use Quick Edit for an individual treatment, or create a Photo Agent project and import a shoot or folder.
2. **Make a first selection.** Rate and pick images, compare similar frames and review local organization suggestions when useful.
3. **Develop a representative photo.** Work through light, color and composition, check detail at 100%, then refine selected areas.
4. **Apply the look to the set.** Save a preset or synchronize selected groups; decide separately whether to include white balance, crops and masks.
5. **Compare versions.** Use snapshots, virtual copies or Agent proposal previews to choose the final treatment.
6. **Prepare delivery.** Select a watermark, output color space, size and naming policy, export the set and save the project for later changes.

## Data, originals and privacy

Originals, editing parameters and derived results are managed separately. Projects reference originals by path, so backups and moves should account for both project data and the photographs themselves.

| File or directory | Contents |
|---|---|
| `.ccraw` | An individual photo's editing project and recipe |
| `.ccrawalbum` | A traditional catalog/filmstrip and its photo adjustments |
| `.ccrawpreset` | A reusable adjustment preset |
| `.ccrawagent` and its matching `.ccrawagent-data` | Agent project manifest, database, previews, derivatives, conversations and execution history |

Basic photo processing is local. The application does not automatically upload photos, logs or telemetry. Conversations send instructions and necessary context to the selected service; Agent uses bounded project summaries and tool results. Editor instructions can include a reduced preview when enabled by the user. Generation receives prompts and explicitly selected edited references; original RAW files are not uploaded as references.

API keys are protected with Windows DPAPI and stored separately from source code. Person features, conversations and generation records are local project or user data; review them before sharing a project. See [privacy](docs/user/privacy.md) for external-edit approvals and interrupted requests.

## Frequently asked questions

**Can I edit without configuring AI?**  
Yes. RAW development, manual controls, curves, culling, catalogs, template watermarks and normal export work independently. Add models and services for the capabilities that need them.

**Why does automatic acceleration sometimes show CPU?**  
The route accounts for transfers and cached results. Small images, cache hits and unsupported operations may be faster on CPU. Compute settings show the actual path.

**Why does a natural-language search sometimes match only filenames?**  
Semantic retrieval needs its local model. Without it, search uses filenames, tags and metadata. Use a configured Agent conversation for complex descriptions and combined conditions.

**Can “two people in the image” be an exact condition?**  
Strict person counts use verified annotations and exclude unknown counts. Detection and anonymous clustering provide supporting signals.

**How do editor previews, Agent previews and exports differ?**  
The editor supports original-pixel region inspection. Standard Agent comparisons are bounded 8-bit JPEGs, with original-pixel comparison available in the application. Approved local proposals render from originals; normal exports follow the selected size, depth and color space.

**What if a watermark font or lens profile does not work?**  
Check that fonts, logos and profiles remain accessible, and review supported fields. An upstream missing font uses fallback behavior; external ICC, DCP and lens XML files should remain available with your editing environment.

## Documentation and contributing

See the [documentation index](docs/README.md) for all guides. User guides are in `docs/user/`; architecture and build instructions are in `docs/development/`.

| Topic | Guide |
|---|---|
| Professional controls, color, masks and output boundaries | [Professional editing](docs/user/editing.md) |
| Culling, catalogs, filters, proxies and files | [Catalog management](docs/user/catalog.md) |
| Agent projects, models, search and execution | [Photo Agent](docs/user/photo-agent.md) |
| Tools, editable parameters and MCP | [Agent tools and MCP](docs/user/agent-tools.md) |
| Watermark templates, layout and sources | [Watermark templates](docs/user/watermark.md) |
| Shared settings, caches, tasks and recovery | [System settings](docs/user/settings.md) |
| Model downloads, purposes and licenses | [Model resources](docs/user/models.md) |
| Data transmission and local information | [Privacy](docs/user/privacy.md) |
| Development, architecture and packaging | [Contributing](CONTRIBUTING.md) · [Architecture](docs/development/architecture.md) · [Windows builds](docs/development/releasing.md) |

Use [GitHub Issues](https://github.com/wamellia/CCRaw/issues) for reproducible problems, workflow feedback and feature suggestions. Start with the contribution guide for code changes. Include the application version, Windows environment and reproduction steps, and review private information before sharing logs or photographs.

## Repository layout

| Directory | Contents |
|---|---|
| `ccraw/` | Application source grouped into imaging, recipes, catalogs, Agent, UI and resources |
| `requirements/` | Base, Agent, development and optional Windows GPU environments |
| `tools/` | Maintenance tools grouped as assets, models, packaging, benchmarks, diagnostics and release |
| `tests/` | Functional, compatibility and runtime-resource tests |
| `docs/` | User guides, development references and interface screenshots |
| `assets/` | Optional runtime manifests, model metadata and licenses |

Start source runs with `main.py` or `run-source.cmd`; build and publication entry points are `build.ps1`, `build-release.cmd` and `publish-release.cmd`. Public `ccraw.model` and `ccraw.engine` imports and saved recipe compatibility are retained. See [architecture](docs/development/architecture.md) for source responsibilities.

## Open source and acknowledgements

CCRaw is developed and maintained by CCRaw contributors. Project source code uses the **MIT** license; see [LICENSE](LICENSE).

Thanks to the Python, Qt/PySide, NumPy, OpenCV, LibRaw and LittleCMS communities, and to the projects supporting image processing, model inference, fonts and development tools. See [THIRD_PARTY.md](THIRD_PARTY.md) for component purposes, official sources, version references and license categories.

Bundled components, models, fonts and artwork retain their own terms. Their license files accompany the corresponding resources; see [THIRD_PARTY.md](THIRD_PARTY.md).
