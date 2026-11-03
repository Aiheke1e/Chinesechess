"""Keep user-selected image originals in a durable local directory."""
from pathlib import Path
import shutil
from uuid import uuid4
from PIL import Image


def import_image(path, directory):
    source = Path(path)
    try:
        with Image.open(source) as image:
            extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(image.format)
            if extension is None:
                raise ValueError("请选择 PNG、JPG 或 WebP 图片")
            image.verify()
    except Image.DecompressionBombError as error:
        raise ValueError("图片尺寸过大") from error
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{uuid4().hex}{extension}"
    try:
        shutil.copyfile(source, destination)
    except OSError:
        destination.unlink(missing_ok=True)
        raise
    return str(destination)
