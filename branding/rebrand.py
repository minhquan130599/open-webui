"""Rebrand an Open WebUI v0.13.3 image (deployment covered by an enterprise license / written permission).

Runs once during `docker build`:
  1. replaces every logo / favicon / splash / app icon with the files in /tmp/branding/assets/<LOGO_STYLE>
  2. replaces the visible name "Open WebUI" with BRAND_NAME in the pre-built frontend
  3. replaces the few hard-coded backend names (authenticator issuer, API docs title, ...)

Kept on purpose: the copyright attribution "Open WebUI Inc." and the external
"Open WebUI Community" site, which are not this deployment's name.
"""

import os
import re
import shutil
import sys
from pathlib import Path

BRAND = os.environ.get('BRAND_NAME', 'NCS AI')
STYLE = os.environ.get('LOGO_STYLE', 'light')  # light | blue
ASSETS = Path('/tmp/branding/assets') / STYLE
if not ASSETS.is_dir():
    sys.exit(f'unknown LOGO_STYLE {STYLE!r}: expected one of light, blue')
BUILD = Path('/app/build')
BACKEND = Path('/app/backend/open_webui')

# ---------------------------------------------------------------- 1. images
ICON_FILES = [
    'favicon.png',
    'favicon-96x96.png',
    'favicon.ico',
    'favicon.svg',
    'apple-touch-icon.png',
    'web-app-manifest-192x192.png',
    'web-app-manifest-512x512.png',
    'logo.png',
    'splash.png',
    'splash-dark.png',
]
targets = [BUILD / 'static', BACKEND / 'static']
copied = 0
for name in ICON_FILES:
    src = ASSETS / name
    if not src.exists():
        sys.exit(f'missing asset {src}')
    for folder in targets:
        if folder.is_dir():
            shutil.copyfile(src, folder / name)
            copied += 1
# root favicon served at /favicon.png
shutil.copyfile(ASSETS / 'favicon.png', BUILD / 'favicon.png')
print(f'[rebrand] images ({STYLE}): {copied + 1} files replaced')

# ---------------------------------------------------------------- 2. frontend text
# Not replaced: "Open WebUI Inc" (copyright), "Open WebUI Community" (external site),
# and `.includes("Open WebUI")`, which the About page uses to detect a custom name.
pattern = re.compile(r'(?<!includes\(")(?<!includes\(\')Open WebUI(?! Inc| Community)')
text_ext = {'.js', '.html', '.json', '.xml', '.webmanifest', '.txt', '.svg'}
files_changed = hits = 0
for path in BUILD.rglob('*'):
    if not path.is_file() or path.suffix not in text_ext:
        continue
    try:
        text = path.read_text(encoding='utf-8')
    except (UnicodeDecodeError, OSError):
        continue
    new, n = pattern.subn(BRAND, text)
    if n:
        path.write_text(new, encoding='utf-8')
        files_changed += 1
        hits += n
print(f'[rebrand] frontend: "Open WebUI" -> "{BRAND}" in {hits} places, {files_changed} files')

# ---------------------------------------------------------------- 3. backend names
backend_edits = [
    # Default display name (sidebar, browser tab, manifest) when WEBUI_NAME is not set in the environment
    ('env.py', "WEBUI_NAME = os.getenv('WEBUI_NAME', 'Open WebUI')", f"WEBUI_NAME = os.getenv('WEBUI_NAME', '{BRAND}')"),
    ('utils/mfa.py', "issuer_name='Open WebUI'", f"issuer_name='{BRAND}'"),  # name in authenticator apps
    ('main.py', "title='Open WebUI',", f"title='{BRAND}',"),  # API docs title
    ('utils/headers.py', "'X-Title': 'Open WebUI',", f"'X-Title': '{BRAND}',"),  # app name sent to providers
    ('utils/oauth.py', "client_name='Open WebUI',", f"client_name='{BRAND}',"),  # OAuth client registration
    ('routers/channels.py', "f'{channel.name} / Open WebUI'", f"f'{{channel.name}} / {BRAND}'"),  # notification title
]
for rel, old, new in backend_edits:
    path = BACKEND / rel
    text = path.read_text(encoding='utf-8')
    if old in text:
        path.write_text(text.replace(old, new), encoding='utf-8')
        print(f'[rebrand] backend: {rel} updated')
    else:
        print(f'[rebrand] backend: {rel} pattern not found, skipped')

# Error message shown in the UI when a model provider is unreachable (several routers).
error_old = 'Open WebUI: Server Connection Error'
error_new = f'{BRAND}: Server Connection Error'
error_files = 0
for path in BACKEND.rglob('*.py'):
    text = path.read_text(encoding='utf-8')
    if error_old in text:
        path.write_text(text.replace(error_old, error_new), encoding='utf-8')
        error_files += 1
print(f'[rebrand] backend: connection error message updated in {error_files} files')
