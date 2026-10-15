# Maintainer tools

Run these commands from the repository root in a prepared environment. Python
tools locate repository resources from their own file paths, so absolute script
paths also work from another directory. Explicit input and output paths remain
relative to the caller's current directory unless the tool documents otherwise.
The Windows build and benchmark wrappers select the repository root themselves.

| Directory | Purpose | Example |
| --- | --- | --- |
| `assets/` | Verify, fetch, bundle, and generate runtime or brand assets | `python tools/assets/fetch_assets.py --verify-only` |
| `packaging/` | Check dependencies, collect licenses, build and verify Windows packages | `powershell -NoProfile -File tools/packaging/build_release.ps1 -PythonPath .venv/Scripts/python.exe` |
| `models/` | Export image models and generated GPU graphs; install optional local agent models | `python tools/models/agent_models.py --verify-only` |
| `benchmarks/` | Measure pixel pipelines, GPUs, and interactive preview behavior | `tools\benchmarks\run_gpu.cmd` |
| `diagnostics/` | Report available compute providers and GPU details | `python tools/diagnostics/check_gpu.py` |
| `release/` | Upload prepared release artifacts to an existing tag | `powershell -NoProfile -File tools/release/publish_release.ps1 -?` |

The root `build.ps1`, `build-release.cmd`, and `publish-release.cmd` are convenient
entry points. Build requirements live in `requirements/`; model provenance and
release procedures are documented in `docs/user/models.md` and
`docs/development/releasing.md`.

`fetch_assets.py` downloads assets only when invoked without `--verify-only`.
`agent_models.py` likewise installs models unless `--verify-only` is supplied.
Model exporters require the appropriate development dependencies and pretrained
weights; the person exporter can download official TorchVision weights.
Benchmarks perform real image processing and can take substantial time.

The build script can create a release environment and install locked dependencies
when no `-PythonPath` is supplied. With `-PythonPath`, prepare that environment
beforehand. Building does not publish. The release script requires explicit
repository, tag, notes, and artifact arguments, creates a draft by default, and
publishes immediately only with `-Publish`.
