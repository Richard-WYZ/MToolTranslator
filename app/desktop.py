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
                "http://127.0.0.1:8000/api/translation/dirty-state",
                timeout=1,
            )
            state = resp.json()
        except Exception:
            state = {"dirty": False}

        if state.get("dirty"):
            confirmed = window.create_confirmation_dialog(
                "确认退出",
                "当前存在未导出或未完成的翻译结果。退出会停止翻译服务和后台进程，"
                "临时文件会保留，是否退出？",
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
        window = webview.create_window(
            title="MTool 汉化工具",
            url="http://127.0.0.1:8000",
            width=1200,
            height=800,
            min_size=(800, 600),
            js_api=Api(),
        )

        on_closing = build_close_handler(window)

        try:
            window.events.closing += on_closing
        except Exception:
            pass

        # 4. Block until the window is closed
        webview.start()

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
