# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

block_cipher = None
root = os.path.abspath('.')

a = Analysis(
    ['main.py'],
    pathex=[root],
    binaries=[],
    datas=[
        ('ui/static', 'ui/static'),
        ('ui/templates', 'ui/templates'),
    ],
    hiddenimports=[
        'fastapi', 'uvicorn', 'webview', 'python_multipart', 'aiohttp',
        'csv', 'json', 'requests', 'urllib.request',
        'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        'starlette', 'starlette.routing', 'starlette.middleware',
        'pydantic', 'pydantic.deprecated', 'pydantic.v1', 'pydantic_core', 'typing_extensions',
        'webview.http', 'webview.platforms.winforms', 'webview.platforms.edgechromium',
        'pythonnet', 'clr',
        'multipart', 'urllib3',
        'uuid', 'tempfile', 'shutil', 'threading', 'time', 'platform', 'subprocess', 're', 'pathlib',
        'app.desktop', 'app.main',
        'translation.settings', 'translation.runtime', 'translation.translate',
        'translation.workflow.pipeline', 'translation.workflow.execution',
        'translation.workflow.review', 'translation.models.api_client',
        'translation.models.ollama_client', 'translation.terminology.glossary',
        'translation.review.summary',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pyi_rth_stdio.py'],
    excludes=['pytest', 'unittest', 'pdb', 'tkinter', 'matplotlib', 'numpy', 'pandas',
              'scipy', 'PIL', 'Pillow', 'django', 'flask', 'jinja2', 'sqlalchemy',
              'asyncio.test', 'cryptography', 'OpenSSL', 'botocore', 'boto3'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='MToolTranslator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
