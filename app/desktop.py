"""
Desktop mode — pywebview window launcher.

Usage:
    python main.py --desktop

Launches a pywebview window pointing at the local FastAPI backend.
Closing the window triggers a clean process exit.
"""

import os
import shutil
import sys
import threading
import time
import requests

# PyInstaller GUI mode: sys.stdout/stderr are None, which breaks many libraries
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

import uvicorn
import webview

from app.services.desktop_sources import desktop_source_registry


class Api:
    """pywebview JS-Python bridge API for desktop features."""

    def save_file_dialog(self, default_filename):
        """Open a native save-file dialog and return the chosen path.

        Returns:
            str or None: The full path the user chose, or None if cancelled.
        """
        try:
            result = webview.windows[0].create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=default_filename,
                file_types=("JSON files (*.json)", "All files (*.*)"),
            )
            # Depending on the GUI backend, pywebview may return one path as
            # a string or wrap it in a tuple/list.
            if isinstance(result, str):
                return result or None
            if result and len(result) > 0:
                return str(result[0])
            return None
        except Exception:
            return None

    def choose_source_file(self):
        """Choose one JSON source and return a trusted one-time import token."""
        try:
            result = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("JSON files (*.json)", "All files (*.*)"),
            )
            if isinstance(result, str):
                selected = result
            elif result and len(result) > 0:
                selected = str(result[0])
            else:
                return None
            return desktop_source_registry.register(selected).public()
        except Exception:
            return None

    def consume_dropped_file_path(self, filename, size):
        """Wait briefly for the native WebView2 drop event and return its token."""
        record = desktop_source_registry.wait_for_match(str(filename), int(size), timeout=2.0)
        return record.public() if record else None

    def copy_file(self, source_path, dest_path):
        """Copy a file from source to destination.

        Returns:
            dict: {"ok": True, "dest_path": dest_path} on success,
                  {"ok": False, "error": message} on failure.
        """
        try:
            shutil.copy2(source_path, dest_path)
            return {"ok": True, "dest_path": dest_path}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def start_server(app):
    """Start the FastAPI/uvicorn backend in a background thread."""
    # Disable uvicorn's default logging to avoid 'isatty' crash in PyInstaller GUI mode
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="warning",
        log_config=None,
        access_log=False,
    )


def build_close_handler(window, request_client=requests):
    """Build a non-reentrant desktop close guard using pywebview's native dialog."""
    def on_closing():
        try:
            resp = request_client.get(
                "http://127.0.0.1:8000/api/desktop/exit-state",
                timeout=1,
            )
            state = resp.json()
        except Exception:
            state = {"requires_confirmation": False}

        if state.get("requires_confirmation", state.get("dirty", False)):
            confirmed = window.create_confirmation_dialog(
                "确认退出",
                "当前仍有翻译或 AI 复核正在运行。退出会停止后台任务，"
                "已保存的断点与临时结果会保留，是否退出？",
            )
            if not confirmed:
                return False
        try:
            request_client.post(
                "http://127.0.0.1:8000/api/desktop/shutdown",
                timeout=1,
            )
        except Exception:
            pass
        return True

    return on_closing


def run_desktop(app):
    """Launch the desktop window and block until it closes.

    Args:
        app: FastAPI application instance (passed from main.py to avoid circular imports).
    """
    try:
        # 1. Start uvicorn in a daemon thread so it dies when main exits
        server_thread = threading.Thread(
            target=start_server, args=(app,), daemon=True
        )
        server_thread.start()

        # 2. Brief wait for the server to be ready
        time.sleep(2)

        # 3. Create the pywebview window (no address bar by default)
        api = Api()
        window = webview.create_window(
            title="MTool 汉化工具",
            url="http://127.0.0.1:8000",
            width=1200,
            height=800,
            min_size=(800, 600),
            js_api=api,
        )

        on_closing = build_close_handler(window)

        try:
            window.events.closing += on_closing
        except Exception:
            pass

        # 4. Block until the window is closed
        drop_binding = {"ready": False}

        def bind_desktop_drop(*_args):
            if drop_binding["ready"]:
                return
            try:
                from webview.dom import DOMEventHandler

                def capture_dropped_files(event):
                    files = (event or {}).get("dataTransfer", {}).get("files", [])
                    for file_info in files:
                        path = str(file_info.get("pywebviewFullPath") or "")
                        if path:
                            try:
                                desktop_source_registry.register(path)
                            except (OSError, ValueError):
                                pass

                zone = window.dom.get_element("#upload-zone")
                if zone:
                    zone.on(
                        "drop",
                        DOMEventHandler(capture_dropped_files, prevent_default=True),
                    )
                    drop_binding["ready"] = True
            except Exception:
                pass

        try:
            window.events.loaded += bind_desktop_drop
        except Exception:
            pass
        webview.start(bind_desktop_drop)

        # 5. Force clean exit. sys.exit() can leave uvicorn/pywebview worker
        # threads alive after the window disappears, which keeps temp/source
        # files locked on Windows.
        os._exit(0)
    except Exception as e:
        # Log unhandled exceptions to file for debugging in PyInstaller GUI mode
        log_path = os.path.join(os.path.dirname(sys.executable), 'desktop_error.log')
        with open(log_path, 'w', encoding='utf-8') as f:
            import traceback
            f.write(f"Unhandled exception: {e}\n")
            f.write(traceback.format_exc())
        raise
