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
            )
            # pywebview returns a tuple of paths or None
            if result and len(result) > 0:
                return result[0]
            return None
        except Exception as e:
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

        def on_closing():
            try:
                resp = requests.get(
                    "http://127.0.0.1:8000/api/translation/dirty-state",
                    timeout=1,
                )
                state = resp.json()
                if not state.get("dirty"):
                    return True
                confirmed = window.evaluate_js(
                    "confirm('当前存在未导出或未完成的翻译结果。关闭会删除临时 .translated、checkpoint 和动态术语 session，但不会删除源文件。是否关闭？')"
                )
                if not confirmed:
                    return False
                def cancel_later(items):
                    for item in items:
                        try:
                            requests.post(
                                "http://127.0.0.1:8000/api/translation/cleanup",
                                json={
                                    "file_path": item.get("file_path"),
                                    "task_id": item.get("task_id"),
                                    "fast": True,
                                },
                                timeout=0.5,
                            )
                        except Exception:
                            pass

                threading.Thread(target=cancel_later, args=(state.get("states", []),), daemon=True).start()
                return True
            except Exception:
                return True

        def on_closing():
            try:
                resp = requests.get(
                    "http://127.0.0.1:8000/api/translation/dirty-state",
                    timeout=1,
                )
                state = resp.json()
                if state.get("dirty"):
                    confirmed = window.evaluate_js(
                        "confirm('\\u5f53\\u524d\\u5b58\\u5728\\u672a\\u5bfc\\u51fa\\u6216\\u672a\\u5b8c\\u6210\\u7684\\u7ffb\\u8bd1\\u7ed3\\u679c\\u3002\\u9000\\u51fa\\u4f1a\\u505c\\u6b62\\u7ffb\\u8bd1\\u670d\\u52a1\\u548c\\u540e\\u53f0\\u8fdb\\u7a0b\\uff0c\\u4e34\\u65f6\\u6587\\u4ef6\\u4f1a\\u4fdd\\u7559\\uff0c\\u662f\\u5426\\u9000\\u51fa\\uff1f')"
                    )
                    if not confirmed:
                        return False
                try:
                    requests.post(
                        "http://127.0.0.1:8000/api/desktop/shutdown",
                        timeout=1,
                    )
                except Exception:
                    pass
                return True
            except Exception:
                return True

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
