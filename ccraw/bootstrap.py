import os
import sys


def _preload_newest_vc_runtime():
    """AI worker only: load the newest MSVCP140.dll before any bundled copy.

    Windows ML execution providers such as TensorRT for RTX need a current VC++
    runtime; an older MSVCP140.dll loaded first makes them fault
    (microsoft/WindowsML#22).  The GUI process is left untouched.
    """
    import ctypes
    from ctypes import wintypes

    def version(path):
        try:
            api = ctypes.windll.version
            size = api.GetFileVersionInfoSizeW(path, None)
            if not size:
                return ()
            data = ctypes.create_string_buffer(size)
            pointer, length = ctypes.c_void_p(), wintypes.UINT()
            if not api.GetFileVersionInfoW(path, 0, size, data) or not api.VerQueryValueW(
                data, '\\', ctypes.byref(pointer), ctypes.byref(length)
            ):
                return ()
            fixed = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
            return (fixed[2] >> 16, fixed[2] & 0xFFFF, fixed[3] >> 16, fixed[3] & 0xFFFF)
        except Exception:
            return ()

    folders = [
        os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32'),
        getattr(sys, '_MEIPASS', ''),
        os.path.dirname(sys.executable),
    ]
    candidates = [os.path.join(f, 'msvcp140.dll') for f in folders if f]
    candidates = [c for c in candidates if os.path.isfile(c)]
    if candidates:
        try:
            ctypes.WinDLL(max(candidates, key=version))
        except OSError:
            pass


if os.name == 'nt' and '--multiprocessing-fork' in sys.argv:
    _preload_newest_vc_runtime()


def main(argv=None):
    if argv is not None:
        sys.argv = [sys.argv[0], *argv]
    if '--version' in sys.argv[1:]:
        from ccraw import __version__

        print('CCRaw ' + __version__)
        return 0
    if '--help' in sys.argv[1:] or '-h' in sys.argv[1:]:
        print(
            'CCRaw: ccraw [photo.ccraw | image | album.ccrawalbum | project.ccrawagent ...]\nOptions: --version, --help'
        )
        return 0
    if sys.platform != 'win32':
        print('CCRaw supports Windows only.', file=sys.stderr)
        return 2
    import multiprocessing

    # The frozen app re-enters here to start the AI worker process (spawn).
    multiprocessing.freeze_support()
    if len(sys.argv) >= 3 and (
        sys.argv[1] in ('--merge-test', '--workflow-test', '--release-test')
        or (len(sys.argv) == 4 and sys.argv[1] in ('--smoke-test', '--agent-smoke-test'))
    ):
        # Windowed PyInstaller builds have no Python stderr even when the process
        # handles are redirected. Persist diagnostic failures, including native
        # crashes, so release checks cannot silently disappear.
        from pathlib import Path
        import faulthandler, traceback

        folder = Path(
            sys.argv[3] if sys.argv[1] in ('--smoke-test', '--agent-smoke-test') else sys.argv[2]
        )
        folder.mkdir(parents=True, exist_ok=True)
        log = open(folder / 'diagnostic-runtime.log', 'w', encoding='utf8', buffering=1)
        sys.stdout = sys.stderr = log
        faulthandler.enable(log)
        sys.excepthook = lambda kind, value, tb: traceback.print_exception(
            kind, value, tb, file=log
        )
    if len(sys.argv) >= 3 and sys.argv[1] == '--merge-test':
        from ccraw.merge_diagnostics import run

        raise SystemExit(run(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
    if len(sys.argv) >= 4 and sys.argv[1] == '--workflow-test':
        from ccraw.workflow_diagnostics import run

        raise SystemExit(run(sys.argv[2], sys.argv[3], sys.argv[4:]))
    if len(sys.argv) >= 4 and sys.argv[1] == '--release-test':
        from ccraw.release_diagnostics import run

        raise SystemExit(run(sys.argv[2], sys.argv[3:]))
    if len(sys.argv) == 4 and sys.argv[1] == '--agent-smoke-test':
        from ccraw.photo_agent.diagnostics import run

        raise SystemExit(run(sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == '--smoke-test':
        from ccraw.diagnostics import run

        raise SystemExit(run(sys.argv[2], sys.argv[3]))
    from ccraw.app import main as run_gui

    return run_gui()
