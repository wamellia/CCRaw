# Optional model assets

CCRaw uses optional local models for two groups of capabilities. Manual photo editing, catalog management and normal export work without these weights. Run the commands below from the repository root using the same Python environment as the application.

| Capability | Resources and installation |
|---|---|
| Photo Agent semantic retrieval and anonymous person clustering | Verified CLIP, tokenizer, YuNet and SFace files from the [Agent model manifest](../../ccraw/resources/models/agent-models.json); install from Settings → AI and services → Local models or use the CLI below |
| Photography upscaling, denoising/restoration, sky/person/foreground/depth selections and local SenseVoice speech recognition | Optional editor runtime resources from the separately distributed [runtime archive](https://github.com/wamellia/CCRaw/releases/tag/runtime-assets); import and verify with the asset CLI |

Install or verify Agent models:

```powershell
.\.venv\Scripts\python.exe tools/models/agent_models.py
.\.venv\Scripts\python.exe tools/models/agent_models.py --verify-only
```

Import a downloaded editor runtime bundle, substituting its actual filename:

```powershell
.\.venv\Scripts\python.exe tools/assets/fetch_assets.py --archive "CCRaw-RuntimeAssets.zip"
.\.venv\Scripts\python.exe tools/assets/fetch_assets.py --verify-only
```

The Windows installer and portable ZIP include verified Agent models. A desktop build uses these bundled files when a complete user installation is absent. Explicit downloads still write to per-user storage; they never modify the application bundle.

Agent models and editor runtime assets use separate installation paths and manifests. Agent models default to `%LOCALAPPDATA%/CCRaw/agent-models`; `CCRAW_AGENT_MODEL_DIR` can override that location. The [runtime manifest](../../assets/runtime-assets.json) describes editor resources, including the speech model and tokens. Files are checked against the relevant manifest; an installed inference runtime and suitable provider are also required to use a model. Optional acceleration depends on the environment, while CPU inference is available for supported models.

The JSON files in [assets/models/](../../assets/models/) record concrete architectures, source weights, native scales and conversion metadata. Large models are excluded from the lightweight Python wheel and source archive. The base package includes its Chinese font and OFL license. Maintainers can rebuild the corresponding ONNX resources with `tools/models/convert_*.py`; conversions must retain provenance, precision checks and updated checksums.

Third-party model names, sources and license texts are preserved. See [third-party components](../../THIRD_PARTY.md), the license texts in [assets/](../../assets/) and [package licenses](../../ccraw/resources/licenses/). The CCRaw MIT license covers project code and does not replace model licenses. See [Photo Agent](photo-agent.md) for retrieval limits and [privacy](privacy.md) for model/service data handling.
