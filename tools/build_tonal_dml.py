"""Write the generated GPU pixel graphs to .onnx files for inspection (e.g. in Netron).

the graphs are built at runtime by ccraw/gpu_graphs.py without the
`onnx` package; this tool only exports them.  Usage: python tools/build_tonal_dml.py [folder]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ccraw import gpu_graphs

folder = Path(sys.argv[1] if len(sys.argv) > 1 else 'gpu-graphs')
folder.mkdir(parents=True, exist_ok=True)
for kind in ('tonal', 'color', 'fused'):
    path = folder / f'ccraw-{kind}.onnx'
    path.write_bytes(gpu_graphs.model(kind))
    print(f'{path}: {path.stat().st_size} bytes')
