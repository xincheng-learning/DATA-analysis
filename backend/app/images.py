"""Validated static images stored inside the existing local session directory."""
import base64
import hashlib
import io
import re

from PIL import Image, ImageOps, UnidentifiedImageError

from app.tools.file_storage import local_session_dir
from app.tools.ingest import IngestionError

MAX_IMAGE_BYTES = 10 * 1024 * 1024
FORMATS = {'PNG': ('png', 'image/png'), 'JPEG': ('jpg', 'image/jpeg'), 'WEBP': ('webp', 'image/webp')}


def image_path(sid, image_id):
    if not re.fullmatch(r'[a-f0-9]{64}\.(png|jpg|webp)', image_id):
        raise ValueError('图片编号无效，请重新上传。')
    directory = local_session_dir(sid) / '_images'
    path = directory / image_id
    if directory.resolve() != directory or path.resolve() != path:
        raise ValueError('图片路径不能重定向至其他目录。')
    return path


def save_image(sid, filename, stream):
    raw = stream.read(MAX_IMAGE_BYTES + 1)
    if len(raw) > MAX_IMAGE_BYTES:
        raise IngestionError('图片超过 10 MB，请缩小后上传。')
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in FORMATS or getattr(source, 'n_frames', 1) != 1:
                raise IngestionError('只支持静态 PNG、JPEG、WebP 图片。')
            if max(source.size) > 8192 or source.width * source.height > 20_000_000:
                raise IngestionError('图片每边不能超过 8192 像素，总像素不能超过 2000 万。')
            fmt = source.format
            image = ImageOps.exif_transpose(source)
            image.load()
            image.info.clear()
            output = io.BytesIO()
            image.save(output, format=fmt, **({'quality':95} if fmt in {'JPEG','WEBP'} else {}))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise IngestionError('图片损坏或格式无效，请重新导出后上传。') from exc
    data = output.getvalue()
    if len(data) > MAX_IMAGE_BYTES:
        raise IngestionError('解码后的图片超过 10 MB，请缩小后上传。')
    extension, mime = FORMATS[fmt]
    image_id = hashlib.sha256(data).hexdigest() + '.' + extension
    path = image_path(sid, image_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)
    return {'id':image_id,'filename':filename,'mime':mime,'width':image.width,'height':image.height,'size':len(data)}


def read_image(sid, reference):
    path = image_path(sid, reference.id)
    if not path.is_file():
        raise ValueError('图片不存在或不属于当前聊天，请重新上传。')
    raw = path.read_bytes()
    mime = next(mime for extension, mime in FORMATS.values() if path.suffix == '.' + extension)
    return {'id':reference.id,'filename':reference.filename,'mime':mime,'size':len(raw)}, {
        'type':'image_url','image_url':{'url':f'data:{mime};base64,{base64.b64encode(raw).decode("ascii")}','detail':'high'}}
