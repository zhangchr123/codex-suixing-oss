"""Bounded, authenticated image transport; no user-controlled filesystem paths."""
import base64
import binascii
import hashlib
import os
from pathlib import Path
import re
import struct
import tempfile
import threading

MAX_IMAGE = 2 * 1024 * 1024
MAX_COUNT = 4
MAX_STORE = 256 * 1024 * 1024
IMAGE_ID = re.compile(r"^[0-9a-f]{64}\.(?:jpg|png)$")


def image_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 33 and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        extension, mime = "png", "image/png"
    elif data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9"):
        offset, width, height = 2, 0, 0
        while offset < len(data) - 4:
            if data[offset] != 255:
                break
            while offset < len(data) and data[offset] == 255:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in (0xD9, 0xDA):
                break
            if marker in (0x01, *range(0xD0, 0xD8)):
                continue
            length = int.from_bytes(data[offset:offset+2], "big")
            if length < 2 or offset + length > len(data):
                break
            if marker in (0xC0, 0xC1, 0xC2) and length >= 8:
                height, width = struct.unpack(">HH", data[offset+3:offset+7])
                break
            offset += length
        extension, mime = "jpg", "image/jpeg"
    else:
        raise ValueError("仅支持 JPEG 或 PNG 图片")
    if not 0 < width <= 4096 or not 0 < height <= 4096:
        raise ValueError("图片尺寸无效或超过 4096 像素")
    return extension, mime


class ImageStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.Lock()

    def accept(self, rows):
        if not isinstance(rows, list) or len(rows) > MAX_COUNT:
            raise ValueError("每条消息最多 4 张图片")
        checked = []
        for row in rows:
            value = row.get("data") if isinstance(row, dict) else None
            if not isinstance(value, str) or len(value) > ((MAX_IMAGE + 2) // 3) * 4:
                raise ValueError("每张图片最多 2 MiB")
            try:
                data = base64.b64decode(value, validate=True)
            except (ValueError, binascii.Error):
                raise ValueError("图片内容无效") from None
            if not 0 < len(data) <= MAX_IMAGE:
                raise ValueError("每张图片最多 2 MiB")
            extension, mime = image_type(data)
            key = hashlib.sha256(data).hexdigest() + "." + extension
            if "id" in row and row["id"] != key:
                raise ValueError("图片校验失败")
            checked.append((key, mime, data))
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            used = sum(p.stat().st_size for p in self.directory.iterdir() if IMAGE_ID.fullmatch(p.name))
            new = {key: data for key, _, data in checked if not (self.directory / key).exists()}
            if used + sum(map(len, new.values())) > MAX_STORE:
                raise ValueError("图片空间已满，请在电脑上整理后再上传")
            for key, data in new.items():
                fd, temp = tempfile.mkstemp(dir=self.directory, prefix=".upload-")
                try:
                    with os.fdopen(fd, "wb") as output:
                        output.write(data)
                    os.replace(temp, self.directory / key)
                finally:
                    if os.path.exists(temp):
                        os.unlink(temp)
        return [{"id": key, "mime": mime, "size": len(data)} for key, mime, data in checked]

    def read(self, key):
        if not isinstance(key, str) or not IMAGE_ID.fullmatch(key):
            raise ValueError("图片编号无效")
        data = (self.directory / key).read_bytes()
        if len(data) > MAX_IMAGE or hashlib.sha256(data).hexdigest() != key.split(".")[0]:
            raise ValueError("图片校验失败")
        return data, "image/png" if key.endswith(".png") else "image/jpeg"

    def transport(self, rows):
        if not isinstance(rows, list) or len(rows) > MAX_COUNT:
            raise ValueError("图片数量无效")
        return [{"id": row["id"], "data": base64.b64encode(self.read(row["id"])[0]).decode("ascii")} for row in rows]


def extract_images(text):
    pattern = r"\n*<codex_suixing_images>\n([\s\S]*?)\n</codex_suixing_images>"
    images = []
    for block in re.findall(pattern, text):
        for key in re.findall(r"([0-9a-f]{64}\.(?:jpg|png))", block):
            if key not in images and len(images) < MAX_COUNT:
                images.append(key)
    return re.sub(pattern, "", text).strip(), images
