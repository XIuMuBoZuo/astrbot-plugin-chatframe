#!/usr/bin/env python3
"""Render a trusted bundled chatframe template from a single stdin JSON request.

The caller controls --output-dir; message content cannot select paths or templates.
No network, shell, clipboard, GUI, or QQ access occurs here.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parent
ENGINE = ROOT / 'engine'
TEMPLATE = ROOT / 'templates' / 'default'
ASSET_HASHES = {
    'background.png': 'b42ace385ed9e1923c7327f7e711c8eb13e817d40312a33c2dace7c240c298b5',
    'portrait.png': '9d49078480e187804482fbd3528c5a0ea2f782ec0f861bfb3c3d22519a569541',
    'panel.png': '07d2cbbaef0372be084b0771a2c4fbdd89bf9312a4feab3e166849a51e4a084f',
}
MAX_REQUEST_BYTES = 16384
MAX_TEXT_CHARS = 240
MIN_FONT_SIZE = 24

class InputError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)

def read_request():
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise InputError('INVALID_REQUEST', '请求过大，请缩短文字。')
    try:
        value = json.loads(raw.decode('utf-8'))
    except (UnicodeError, ValueError):
        raise InputError('INVALID_REQUEST', '请求格式无效。') from None
    if not isinstance(value, dict) or set(value) != {'text'} or not isinstance(value['text'], str):
        raise InputError('INVALID_REQUEST', '请求仅接受 text 文字字段。')
    text = value['text'].replace('\r\n', '\n').replace('\r', '\n')
    if not text.strip():
        raise InputError('INVALID_REQUEST', '请提供需要排版的文字。')
    if len(text) > MAX_TEXT_CHARS:
        raise InputError('TEXT_TOO_LONG', f'文字超过 {MAX_TEXT_CHARS} 字，请缩短后再试。')
    if any((ord(ch) < 32 and ch not in '\n\t') or ord(ch) == 127 for ch in text):
        raise InputError('INVALID_REQUEST', '文字中含无法显示的控制字符。')
    return text

def render(text):
    sys.path.insert(0, str(ENGINE))
    import project_store as store
    import render_text
    import renderer
    import text_flow
    from scene import Scene

    try:
        config_bytes=(TEMPLATE/'scene.json').read_bytes()
        if len(config_bytes)>65536:
            raise ValueError('scene too large')
        spec=json.loads(config_bytes.decode('utf-8'))
        for layer in spec.get('layers',[]):
            if layer.get('kind')=='image':
                name=layer.get('image_path')
                if name not in ASSET_HASHES:
                    raise ValueError('untrusted image path')
                asset=TEMPLATE/name
                if hashlib.sha256(asset.read_bytes()).hexdigest()!=ASSET_HASHES[name]:
                    raise ValueError('invalid asset')
                layer['image_path']=str(asset.resolve())
            elif layer.get('kind')=='text':
                if layer.get('font',{}).get('family')!='@windows-cjk':
                    raise ValueError('untrusted font path')
        scene=Scene.from_dict(spec)
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        raise InputError('TEMPLATE_INVALID', '内置模板无效，请检查本机插件文件。') from None
    if scene.text_mode!='repeat' or scene.output_width>2048 or scene.output_width**2*scene.height/scene.width>2_500_000:
        raise InputError('TEMPLATE_INVALID', '内置模板文字模式或尺寸不适用。')
    windir=Path(os.environ.get('WINDIR',r'C:\Windows'))
    font=next((windir/'Fonts'/name for name in ['msyh.ttc','msjh.ttc','simsun.ttc','simhei.ttf'] if (windir/'Fonts'/name).is_file()),None)
    if font is None:
        raise InputError('FONT_UNAVAILABLE', '未找到所需的 Windows 中文字体。')
    try:
        from PIL import ImageFont
        ImageFont.truetype(str(font),24)
    except OSError:
        raise InputError('FONT_UNAVAILABLE', 'Windows 中文字体无法加载。') from None
    visible=[layer for layer in scene.layers if layer.kind=='text' and layer.visible and layer.opacity]
    if not visible:
        raise InputError('TEMPLATE_INVALID', '内置模板没有可用的文字框。')
    # Keep sentence-final punctuation verbatim in this process only.
    text_flow.trim_frame_punctuation=lambda value:value
    for layer in visible:
        layer.font.family=str(font)
        layer.font.auto_shrink=True
        region=text_flow.text_region(layer)
        layout=render_text.layout_text(text,region,layer.font,store.resolve_font(layer.font.family))
        if (layout.font_size<MIN_FONT_SIZE or layout.block_height>region[3]-region[1]
                or layout.block_width>region[2]-region[0]):
            raise InputError('TEXT_OVERFLOW', '这段文字放入模板后会太小或被截断，请缩短后再试。')
    data,result=renderer.render_to_bytes(scene,message=text,format='png')
    if result.text_overflow or result.flow_error or not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise InputError('TEXT_OVERFLOW', '文字没有完整放入画面，请缩短后再试。')
    if len(data)>15*1024*1024:
        raise InputError('RENDER_FAILED', '图片文件过大。')
    return data,result.out_size

def main():
    if len(sys.argv)!=3 or sys.argv[1]!='--output-dir' or not sys.argv[2].strip():
        raise InputError('INVALID_REQUEST', '运行参数不完整。')
    text = read_request()
    png, (width, height) = render(text)
    out = Path(sys.argv[2]).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    target = out / ('chatframe-' + uuid.uuid4().hex + '.png')
    with target.open('xb') as f:
        f.write(png)
    return {'ok': True, 'image_path': str(target), 'width': width, 'height': height}

if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=True), flush=True)
    except InputError as exc:
        print(json.dumps({'ok': False, 'error': {'code': exc.code, 'message': exc.message}}, ensure_ascii=True), flush=True)
        sys.exit(2)
    except Exception:
        # Never print user text, local paths or a traceback into a chat response.
        print(json.dumps({'ok': False, 'error': {'code': 'RENDER_FAILED', 'message': '聊天框图片暂时无法生成。'}}, ensure_ascii=True), flush=True)
        sys.exit(1)
