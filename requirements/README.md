# Python environments

Use Windows x64 and Python 3.12. Run installation commands from the repository
root inside a dedicated virtual environment.

| File | Purpose |
| --- | --- |
| [base.txt](base.txt) | Desktop editor and local photo-processing dependencies |
| [agent.txt](agent.txt) | Base dependencies plus the Photo Agent runtime and tokenizer |
| [dev.txt](dev.txt) | Agent environment plus tests, formatting and packaging tools |
| [base.lock.txt](base.lock.txt) | Exact versions for the desktop build environment, including validation and packaging tools |
| [agent.lock.txt](agent.lock.txt) | Exact versions for the build environment with Photo Agent enabled |
| [windows-directml.txt](windows-directml.txt) | Windows ML / DirectML runtime variant |
| [windows-cuda.txt](windows-cuda.txt) | Optional CUDA runtime and CuPy variant |

For source development:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements/dev.txt
.venv\Scripts\python.exe main.py
```

For the locked environment used by the complete Windows build:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements/agent.lock.txt
```

Install only one ONNX Runtime distribution per environment. GPU profiles replace
the CPU runtime; follow the comments in the selected profile. Optional model
weights and ExifTool are separate from Python packages. Their setup is described
in [model resources](../docs/user/models.md), and build steps in
[Windows packaging](../docs/development/releasing.md).
