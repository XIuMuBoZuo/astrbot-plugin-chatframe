"""AstrBot pre-send adapter for the Chatframe Studio renderer."""
from __future__ import annotations

import asyncio
import base64
import io
import json
from pathlib import Path
import sys
import tempfile

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
import astrbot.api.message_components as Comp
from astrbot.api.star import Context, Star
from PIL import Image as PillowImage

ROOT = Path(__file__).resolve().parent
RENDERER = ROOT / "renderer" / "render_template.py"
QQ_PLATFORMS = frozenset({"aiocqhttp", "qq_official", "qq_official_webhook"})
MAX_PNG_BYTES = 12 * 1024 * 1024
MAX_RUNS = 4
RENDER_TIMEOUT = 45


def _text_runs(chain):
    """Group consecutive text components while retaining @/reply order.

    Mixed media is left untouched because replacing only part of such a chain
    could alter the order or meaning of a reply.
    """
    preserved = tuple(cls for cls in (getattr(Comp, "At", None),
                                      getattr(Comp, "AtAll", None),
                                      getattr(Comp, "Reply", None)) if isinstance(cls, type))
    groups = []
    pending = []
    for item in chain:
        if isinstance(item, Comp.Plain):
            pending.append(item)
        elif isinstance(item, preserved):
            if pending:
                groups.append(("text", pending))
                pending = []
            groups.append(("keep", item))
        else:
            return None
    if pending:
        groups.append(("text", pending))
    texts = ["".join(item.text for item in value) for kind, value in groups if kind == "text"]
    if len(texts) > MAX_RUNS or not any(text.strip() for text in texts):
        return None
    if any(len(text) > 240 for text in texts):
        return None
    return groups


class ChatframePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._slots = asyncio.Semaphore(2)

    async def _render(self, text: str) -> bytes:
        interpreter = str(self.config.get("python_executable", "") or "").strip()
        if interpreter and not Path(interpreter).is_absolute():
            raise ValueError("python_path")
        interpreter = interpreter or sys.executable
        template = str(self.config.get("template_path", "") or "").strip()
        if template and not Path(template).is_absolute():
            raise ValueError("template_path")
        if getattr(sys, "frozen", False) and not self.config.get("python_executable"):
            raise ValueError("python_required")

        async with self._slots:
            # The generated directory is private and created under the system's
            # selected temp root. Its resolved parent is checked before cleanup.
            temp_root = Path(tempfile.gettempdir()).resolve()
            with tempfile.TemporaryDirectory(prefix="chatframe-astrbot-", dir=temp_root) as tmp:
                out = Path(tmp).resolve()
                if out.parent != temp_root:
                    raise ValueError("temporary_directory")
                command = [interpreter, "-B", str(RENDERER), "--output-dir", str(out)]
                if template:
                    command += ["--template", template]
                proc = await asyncio.create_subprocess_exec(
                    *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                request = json.dumps({"text": text}, ensure_ascii=True).encode("ascii")
                try:
                    stdout, _ = await asyncio.wait_for(proc.communicate(request), timeout=RENDER_TIMEOUT)
                except (TimeoutError, asyncio.CancelledError):
                    if proc.returncode is None:
                        proc.kill()
                        await proc.wait()
                    raise
                if proc.returncode != 0 or len(stdout) > 65536:
                    raise ValueError("render_failed")
                reply = json.loads(stdout)
                if not isinstance(reply, dict) or reply.get("ok") is not True:
                    raise ValueError("render_failed")
                raw_path = Path(str(reply.get("image_path", "")))
                if not raw_path.is_absolute() or raw_path.is_symlink():
                    raise ValueError("invalid_image")
                image_path = raw_path.resolve(strict=True)
                if image_path.parent != out or image_path.suffix.lower() != ".png":
                    raise ValueError("invalid_image")
                if image_path.stat().st_size > MAX_PNG_BYTES:
                    raise ValueError("image_too_large")
                data = image_path.read_bytes()
                if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("invalid_image")
                with PillowImage.open(io.BytesIO(data)) as image:
                    if image.format != "PNG" or image.size != (reply.get("width"), reply.get("height")):
                        raise ValueError("invalid_image")
                    image.verify()
                return data

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """Convert eligible QQ replies before AstrBot sends them."""
        if self.config.get("enabled", False) is not True:
            return
        if event.get_platform_name() not in QQ_PLATFORMS:
            return
        result = event.get_result()
        if result is None or not result.chain:
            return
        if self.config.get("only_ai_replies", True) and not result.is_llm_result():
            return
        groups = _text_runs(result.chain)
        if groups is None:
            return
        new_chain = []
        try:
            for kind, value in groups:
                if kind == "keep":
                    new_chain.append(value)
                    continue
                text = "".join(item.text for item in value)
                if not text.strip():
                    new_chain.extend(value)
                    continue
                image = await self._render(text)
                new_chain.append(Comp.Image.fromBase64(base64.b64encode(image).decode("ascii")))
        except Exception as exc:
            # The host has not attempted a send yet. Preserve the entire original
            # chain, including references and @, rather than lose or duplicate it.
            logger.warning("聊天框转图失败，保留原回复（%s）", type(exc).__name__)
            return
        result.chain = new_chain
        result.use_t2i_ = False
