"""Mirror only explicitly referenced local raster images, with opaque authenticated URLs."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlsplit

MEDIA_ID = re.compile(r'^[0-9a-f]{64}\.(?:png|jpg|gif|webp)$')
MAX_MEDIA = 8 * 1024 * 1024
MAX_STORE = 256 * 1024 * 1024
MARKDOWN_IMAGE = re.compile(r'!\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\n]*?))\s*\)')


class MediaStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    def accept(self, rows):
        if not isinstance(rows, list) or len(rows) > 16:
            raise ValueError('图片批次无效')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        used = sum(p.stat().st_size for p in self.directory.iterdir() if MEDIA_ID.fullmatch(p.name))
        for row in rows:
            key, value = row.get('id', ''), row.get('data', '')
            if not MEDIA_ID.fullmatch(key) or not isinstance(value, str) or len(value) > 4 * ((MAX_MEDIA + 2) // 3):
                raise ValueError('图片无效')
            data = base64.b64decode(value, validate=True)
            if not 0 < len(data) <= MAX_MEDIA or hashlib.sha256(data).hexdigest() != key.split('.')[0]:
                raise ValueError('图片校验失败')
            magic = {'png': b'\x89PNG\r\n\x1a\n', 'jpg': b'\xff\xd8', 'gif': b'GIF8', 'webp': b'RIFF'}[key.rsplit('.', 1)[1]]
            if not data.startswith(magic) or key.endswith('.webp') and data[8:12] != b'WEBP':
                raise ValueError('图片格式无效')
            target = self.directory / key
            if target.exists():
                continue
            if used + len(data) > MAX_STORE:
                raise ValueError('图片预览空间已满')
            fd, temp = tempfile.mkstemp(dir=self.directory, prefix='.media-')
            try:
                with os.fdopen(fd, 'wb') as out:
                    out.write(data)
                os.replace(temp, target)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            used += len(data)

    def read(self, key):
        if not MEDIA_ID.fullmatch(key):
            raise ValueError('图片编号无效')
        data = (self.directory / key).read_bytes()
        if len(data) > MAX_MEDIA or hashlib.sha256(data).hexdigest() != key.split('.')[0]:
            raise ValueError('图片校验失败')
        mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'gif': 'image/gif', 'webp': 'image/webp'}[key.rsplit('.', 1)[1]]
        return data, mime


class MediaMirror:
    def __init__(self, root, codex_home, state_dir=None):
        self.state = Path(state_dir or os.environ.get('CODEX_SUIXING_STATE_DIR') or Path(root) / '.state').resolve()
        self.store = MediaStore(self.state / 'media-local')
        self.cache = {}
        self.codex_home = Path(codex_home).resolve()
        self.root = Path(root).resolve()
        self.error = ''
        try:
            self.relocations = json.loads((self.state / 'relocated-paths.json').read_bytes())
        except (OSError, ValueError):
            self.relocations = {}

    def local_image(self, raw, workspace):
        raw = unquote(raw.strip())
        # Markdown titles are outside angle-bracket destinations.
        raw = re.sub(r'\s+[\"\'][^\"\']*[\"\']$', '', raw)
        if raw.startswith('file://'):
            raw = urlsplit(raw).path.lstrip('/') if os.name == 'nt' else urlsplit(raw).path
        if raw.startswith(('/api/', 'https://', 'http://', 'data:', 'blob:')):
            return None
        path = Path(raw)
        if not path.is_absolute():
            if not workspace:
                return None
            path = Path(workspace) / path
        path = path.resolve()
        for before, after in self.relocations.items():
            try:
                path = (Path(after) / path.relative_to(Path(before).resolve())).resolve()
                break
            except ValueError:
                continue
        roots = [self.codex_home / n for n in ('generated_images', 'visualizations', 'attachments')]
        roots += [self.state / 'incoming']
        if workspace:
            roots.append(Path(workspace).resolve())
        if not any(path.is_relative_to(r) for r in roots) or path.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
            return None
        stat = path.stat()
        if stat.st_size > 40 * 1024 * 1024:
            raise ValueError('原始图片过大')
        signature = (str(path), stat.st_mtime_ns, stat.st_size)
        if signature in self.cache:
            return self.cache[signature]
        from PIL import Image
        with Image.open(path) as source:
            key = self.preview(source)
        self.cache[signature] = key
        if len(self.cache) > 2048:
            self.cache = {signature: key}
        return key

    def inline_image(self, url):
        if not isinstance(url, str) or len(url) > 12 * 1024 * 1024:
            return None
        match = re.fullmatch(r'data:image/(?:png|jpeg|jpg|gif|webp);base64,([A-Za-z0-9+/=\r\n]+)', url)
        if not match:
            return None
        data = base64.b64decode(match[1].replace('\r', '').replace('\n', ''), validate=True)
        if not 0 < len(data) <= MAX_MEDIA:
            return None
        signature = ('inline', hashlib.sha256(data).hexdigest())
        if signature in self.cache:
            return self.cache[signature]
        from PIL import Image
        with Image.open(io.BytesIO(data)) as source:
            key = self.preview(source)
        self.cache[signature] = key
        if len(self.cache) > 2048:
            self.cache = {signature: key}
        return key

    def preview(self, source):
        # A preview is a separate derivative; originals are never overwritten.
        from PIL import ImageOps
        source = ImageOps.exif_transpose(source)
        source.thumbnail((2560, 2560))
        output = io.BytesIO()
        if source.mode in ('RGBA', 'LA') or source.info.get('transparency') is not None:
            source.convert('RGBA').save(output, format='PNG')
            ext = 'png'
        else:
            source.convert('RGB').save(output, format='JPEG', quality=88)
            ext = 'jpg'
        data = output.getvalue()
        key = hashlib.sha256(data).hexdigest() + '.' + ext
        self.store.accept([{'id': key, 'data': base64.b64encode(data).decode('ascii')}])
        return key

    def rewrite(self, threads):
        needed = set()
        self.error = ''
        output = []
        for thread in threads:
            messages = []
            for message in thread.get('messages', []):
                def replace(match):
                    try:
                        key = self.local_image(match.group(1) or match.group(2), thread.get('workspace', ''))
                        if key:
                            needed.add(key)
                            return match.group(0).split('](', 1)[0] + '](/api/media/' + key + ')'
                    except Exception:
                        self.error = '部分本地图片暂不可预览'
                    return match.group(0)
                text = MARKDOWN_IMAGE.sub(replace, message['text'])
                for key in re.findall(r'/api/media/([0-9a-f]{64}\.(?:png|jpg|gif|webp))', text):
                    if (self.store.directory / key).is_file():
                        needed.add(key)
                messages.append({**message, 'text': text})
            output.append({**thread, 'messages': messages})
        return output, needed

    def transport(self, needed, sent):
        rows, size = [], 0
        for key in sorted(needed - sent):
            data, _ = self.store.read(key)
            if len(rows) >= 16 or size + len(data) > 12 * 1024 * 1024:
                break
            rows.append({'id': key, 'data': base64.b64encode(data).decode('ascii')})
            size += len(data)
        return rows
