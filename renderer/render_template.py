#!/usr/bin/env python3
"""Render one trusted, administrator-selected .mscene in an isolated process.

The template path is a process argument, never a message field.  Resources are
validated and materialized under a fresh private temporary directory; the
original template and the workbench's application data are not modified.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
import uuid
import zipfile

import render_card

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_RESOURCE_BYTES = 48 * 1024 * 1024
MAX_EXPANDED_BYTES = 160 * 1024 * 1024
MAX_MEMBERS = 300
MAX_SCENE_BYTES = 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_TOTAL_IMAGE_PIXELS = 100_000_000
MAX_OUTPUT_PIXELS = 8_000_000


def fail(code: str, message: str):
    raise render_card.InputError(code, message)


def validate_member_name(name: str) -> None:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        fail("TEMPLATE_INVALID", "模板素材路径无效。")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in name.split("/")) or ":" in name:
        fail("TEMPLATE_INVALID", "模板素材路径无效。")


def load_scene(archive_path: Path, resource_dir: Path):
    from PIL import Image, ImageFont
    from scene import Scene

    if not archive_path.is_absolute() or archive_path.suffix.lower() != ".mscene":
        fail("TEMPLATE_INVALID", "请在设置中填写 .mscene 模板的绝对路径。")
    if not archive_path.is_file() or archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        fail("TEMPLATE_INVALID", "模板不存在或文件过大。")

    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_MEMBERS or sum(info.file_size for info in entries) > MAX_EXPANDED_BYTES:
            fail("TEMPLATE_INVALID", "模板素材过多或解压后过大。")
        names = [info.filename for info in entries]
        if len(names) != len(set(names)):
            fail("TEMPLATE_INVALID", "模板内有重复的素材名称。")
        infos = {info.filename: info for info in entries}
        scene_info = infos.get("scene.json")
        if scene_info is None or scene_info.file_size > MAX_SCENE_BYTES:
            fail("TEMPLATE_INVALID", "模板缺少有效的 scene.json。")
        data = json.loads(archive.read(scene_info).decode("utf-8"))
        scene = Scene.from_dict(data)
        if scene.output_width**2 * scene.height / scene.width > MAX_OUTPUT_PIXELS:
            fail("TEMPLATE_INVALID", "模板输出尺寸过大。")

        extracted = {}
        total_image_pixels = 0
        for layer in scene.layers:
            resource_name = layer.image_path if layer.kind == "image" else layer.font.family
            validate_member_name(resource_name)
            expected_prefix = "images/" if layer.kind == "image" else "fonts/"
            if not resource_name.startswith(expected_prefix):
                fail("TEMPLATE_INVALID", "模板素材必须包含在模板文件内。")
            key = (resource_name, layer.kind)
            if key not in extracted:
                info = infos.get(resource_name)
                if info is None or info.is_dir() or info.file_size > MAX_RESOURCE_BYTES or info.flag_bits & 1:
                    fail("TEMPLATE_INVALID", "模板素材缺失或无法读取。")
                if stat.S_ISLNK(info.external_attr >> 16):
                    fail("TEMPLATE_INVALID", "模板素材包含不支持的链接。")
                payload = archive.read(info)
                if layer.kind == "image":
                    with Image.open(io.BytesIO(payload)) as image:
                        pixels = image.width * image.height
                        if pixels > MAX_IMAGE_PIXELS:
                            fail("TEMPLATE_INVALID", "模板中的图片尺寸过大。")
                        total_image_pixels += pixels
                        if total_image_pixels > MAX_TOTAL_IMAGE_PIXELS:
                            fail("TEMPLATE_INVALID", "模板中的图片总尺寸过大。")
                        image.verify()
                    suffix = ".img"
                else:
                    ImageFont.truetype(io.BytesIO(payload), 12)
                    suffix = ".font"
                target = resource_dir / (hashlib.sha256(payload).hexdigest() + suffix)
                if not target.exists():
                    target.write_bytes(payload)
                extracted[key] = str(target)
            if layer.kind == "image":
                layer.image_path = extracted[key]
            else:
                layer.font.family = extracted[key]
        return scene


def render_custom(text: str, template_path: Path, output_dir: Path):
    sys.path.insert(0, str(render_card.ENGINE))
    import geometry
    import project_store
    import render_text
    import renderer
    import text_flow

    with tempfile.TemporaryDirectory(prefix="chatframe-assets-", dir=output_dir) as tmp:
        scene = load_scene(template_path, Path(tmp))
        # Replies should retain their punctuation.  This process is isolated, so
        # the original workbench and other plugins are unaffected.
        text_flow.trim_frame_punctuation = lambda value: value
        flow = text_flow.distribute(scene, text)
        if flow.error:
            fail("TEXT_OVERFLOW", "文字无法完整放入该模板。")
        canvas = geometry.rectangle(0, 0, scene.width, scene.height)
        visible = [layer for layer in scene.layers if layer.kind == "text" and layer.visible
                   and layer.opacity > 0 and geometry.intersects(layer.quad, canvas)]
        if not visible:
            fail("TEMPLATE_INVALID", "模板没有可见的文字框。")
        for layer in visible:
            fitted = flow.layers.get(layer.id, layer)
            message = flow.messages.get(layer.id, "")
            if not message.strip():
                continue
            region = text_flow.text_region(fitted)
            font = project_store.resolve_font(fitted.font.family)
            layout = render_text.layout_text(message, region, fitted.font, font,
                                             preserve_whitespace=scene.text_mode == "sequence")
            if layout.block_height > region[3] - region[1] or layout.block_width > region[2] - region[0]:
                fail("TEXT_OVERFLOW", "文字无法完整放入该模板。")
        png, result = renderer.render_to_bytes(scene, message=text, format="png")
        if result.text_overflow or result.flow_error or not png.startswith(b"\x89PNG\r\n\x1a\n"):
            fail("TEXT_OVERFLOW", "文字无法完整放入该模板。")
        if len(png) > 12 * 1024 * 1024:
            fail("RENDER_FAILED", "输出图片过大。")
        return png, result.out_size


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--template", default="")
    args = parser.parse_args()
    text = render_card.read_request()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.template:
        png, (width, height) = render_custom(text, Path(args.template), output_dir)
    else:
        png, (width, height) = render_card.render(text)
    target = output_dir / ("chatframe-" + uuid.uuid4().hex + ".png")
    with target.open("xb") as result:
        result.write(png)
    return {"ok": True, "image_path": str(target), "width": width, "height": height}


if __name__ == "__main__":
    try:
        print(json.dumps(main(), ensure_ascii=True), flush=True)
    except render_card.InputError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": exc.message}},
                         ensure_ascii=True), flush=True)
        sys.exit(2)
    except Exception:
        print(json.dumps({"ok": False, "error": {"code": "RENDER_FAILED",
                         "message": "聊天框图片暂时无法生成。"}}, ensure_ascii=True), flush=True)
        sys.exit(1)
