"""Portable entry point: show startup errors even without a console."""
import ctypes
import importlib.util
import json
from pathlib import Path
import sys
import traceback

if __name__ == '__main__':
    try:
        if len(sys.argv) == 3 and sys.argv[1] == '--check':
            import tkinter as tk
            from main import Application, enable_dpi_awareness
            enable_dpi_awareness()
            root = tk.Tk()
            app = Application(root, restore=False, persist=False)
            root.update()
            Path(sys.argv[2]).write_text(json.dumps(dict(status='passed', executable=sys.executable,
                isolated=bool(sys.flags.isolated), sys_path=sys.path,
                torch_absent=importlib.util.find_spec('torch') is None,
                source_empty=app.source.get() == '', gui=True), indent=2), encoding='utf-8')
            root.destroy()
        else:
            from main import main
            main()
    except Exception:
        log = Path(__file__).resolve().parents[1] / 'launch.log'
        log.write_text(traceback.format_exc(), encoding='utf-8')
        ctypes.windll.user32.MessageBoxW(None, f'Не удалось запустить приложение.\nЖурнал: {log}', 'DLSSNR', 0x10)

