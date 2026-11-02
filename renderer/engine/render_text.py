"""文字渲染 —— 角色名 + 正文，支持自适应字号、自动换行、描边、括号高亮。

保留了上游 `text_fit_draw.py` 里最核心的两项能力：
  * **二分搜索最大字号**：在矩形内找到能装下的最大字号
  * **括号高亮**：`[` `]` `【` `】` 内的文字用角色主色（跨行保持状态）

并新增：
  * 描边模式可选（shadow 偏移阴影 / outline 四周描边 / none）
  * 字号可固定（size>0）或自动（size=0）
  * 角色名独立排版（字体/字号/颜色/描边/偏移/间距）
"""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from PIL import Image, ImageDraw, ImageFont

from models import FontSpec

__all__ = ["TextBlock", "layout_text", "draw_name", "draw_body", "wrap_lines"]

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]


# --------------------------------------------------------------------------- #
# 字体缓存
# --------------------------------------------------------------------------- #
_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def load_font(path: str | None, size: int) -> ImageFont.FreeTypeFont:
    size = max(1, int(size))
    key = (path or "", size)
    if key not in _FONT_CACHE:
        f: ImageFont.FreeTypeFont | None = None
        if path:
            try:
                f = ImageFont.truetype(path, size=size)
            except Exception:
                f = None
        if f is None:
            try:
                f = ImageFont.truetype("DejaVuSans.ttf", size=size)
            except Exception:
                f = ImageFont.load_default()
        _FONT_CACHE[key] = f
    return _FONT_CACHE[key]


def clear_font_cache() -> None:
    _FONT_CACHE.clear()


# --------------------------------------------------------------------------- #
# 换行
# --------------------------------------------------------------------------- #
def wrap_lines(
    text: str, font: ImageFont.FreeTypeFont, max_w: int, draw: ImageDraw.ImageDraw,
    preserve_whitespace: bool = False,
) -> list[str]:
    """把文本按最大宽度折行。含空格按词切，否则逐字切（中文场景）。"""
    if preserve_whitespace:
        return flow_lines(text,font,max_w,draw)[0]
    lines: list[str] = []
    for para in text.replace('\r\n','\n').replace('\r','\n').split('\n'):
        has_space = " " in para
        units = para.split(" ") if has_space else list(para)
        buf = ""

        def join(a: str, b: str) -> str:
            if not a:
                return b
            return (a + " " + b) if has_space else (a + b)

        for u in units:
            trial = join(buf, u)
            if draw.textlength(trial, font=font) <= max_w:
                buf = trial
                continue
            if buf:
                lines.append(buf)
                buf = ""
            # 单个单元本身就超宽 → 逐字再切
            if draw.textlength(u, font=font) > max_w:
                tmp = ""
                for ch in u:
                    if draw.textlength(tmp + ch, font=font) <= max_w:
                        tmp += ch
                    else:
                        if tmp:
                            lines.append(tmp)
                        tmp = ch
                buf = tmp
            else:
                buf = u
        if buf != "":
            lines.append(buf)
        if para == "":
            lines.append("")
    return lines or [""]


def text_clusters(text):
    """Keep combining marks, emoji modifiers and joined emoji with their base."""
    cluster=''; regional_count=0
    for ch in text:
        code=ord(ch); regional=0x1F1E6<=code<=0x1F1FF
        attached=(unicodedata.category(ch) in ('Mn','Mc','Me') or
                  0x1F3FB<=code<=0x1F3FF or ch=='\u200d' or cluster.endswith('\u200d'))
        if cluster and ch!='\n' and cluster!='\n' and (attached or regional and regional_count==1):
            cluster+=ch; regional_count=regional_count+1 if regional else 0
        else:
            if cluster: yield cluster
            cluster=ch; regional_count=int(regional)
    if cluster: yield cluster


def flow_lines(text,font,max_w,draw,max_lines=None):
    """Wrap without deleting spaces; return lines and the exact consumed prefix length."""
    lines=['']; consumed=0
    closing='，。！？；：、,.!?;:)]}》】）〉」』’”％%…'
    opening='([{《【（〈「『‘“'
    for cluster in text_clusters(text.replace('\r\n','\n').replace('\r','\n')):
        if cluster=='\n':
            if max_lines is not None and len(lines)>=max_lines: break
            lines.append('')
        elif draw.textlength(lines[-1]+cluster,font=font)<=max_w:
            lines[-1]+=cluster
        else:
            if not lines[-1]:
                if max_lines is not None: break
                lines[-1]=cluster
            else:
                carry=''; previous=list(text_clusters(lines[-1]))
                if cluster in closing:
                    start=len(previous)-1
                    while start>0 and previous[start] in closing: start-=1
                    if previous[start] not in closing: carry=''.join(previous[start:])
                elif previous[-1] in opening:
                    carry=previous[-1]
                if max_lines is not None and (len(lines)>=max_lines or
                        draw.textlength(carry+cluster,font=font)>max_w):
                    consumed-=len(carry)
                    break
                if carry: lines[-1]=lines[-1][:-len(carry)]
                lines.append(carry+cluster)
        consumed+=len(cluster)
    return lines,consumed


def fit_text_prefix(text,region,spec,font_path):
    """Fit a prefix at the selected size, without shrinking all text into one frame."""
    font=load_font(font_path,spec.size or spec.max_size)
    draw=ImageDraw.Draw(Image.new('RGB',(8,8)))
    _width,_height,line_height=_measure([''],font,draw,spec.line_spacing)
    capacity=max(0,(region[3]-region[1])//line_height)
    if not capacity: return ''
    _lines,count=flow_lines(text,font,max(1,region[2]-region[0]),draw,capacity)
    return text[:count]


def _measure(
    lines: list[str], font: ImageFont.FreeTypeFont, draw: ImageDraw.ImageDraw,
    line_spacing: float,
) -> tuple[int, int, int]:
    ascent, descent = font.getmetrics()
    line_h = max(1, int((ascent + descent) * (1.0 + line_spacing)))
    max_w = 0
    for ln in lines:
        max_w = max(max_w, int(draw.textlength(ln, font=font)))
    total_h = max(line_h * max(1, len(lines)), 1)
    return max_w, total_h, line_h


# --------------------------------------------------------------------------- #
# 括号高亮分段
# --------------------------------------------------------------------------- #
def _segments(
    s: str, in_bracket: bool, base: RGBA, bracket: RGBA
) -> tuple[list[tuple[str, RGBA]], bool]:
    segs: list[tuple[str, RGBA]] = []
    buf = ""
    for ch in s:
        if ch in "[【":
            if buf:
                segs.append((buf, bracket if in_bracket else base))
                buf = ""
            segs.append((ch, bracket))
            in_bracket = True
        elif ch in "]】":
            if buf:
                segs.append((buf, bracket))
                buf = ""
            segs.append((ch, bracket))
            in_bracket = False
        else:
            buf += ch
    if buf:
        segs.append((buf, bracket if in_bracket else base))
    return segs, in_bracket


# --------------------------------------------------------------------------- #
# 排版结果
# --------------------------------------------------------------------------- #
@dataclass
class TextBlock:
    lines: list[str]
    font: ImageFont.FreeTypeFont
    font_size: int
    line_height: int
    block_height: int
    block_width: int
    y_start: int


def layout_text(
    text: str,
    region: tuple[int, int, int, int],
    spec: FontSpec,
    font_path: str | None,
    bracket_color: RGB | None = None,
    probe: ImageDraw.ImageDraw | None = None,
    preserve_whitespace: bool = False,
) -> TextBlock:
    """在 region 内排版文字，返回排版结果（不绘制）。"""
    x1, y1, x2, y2 = region
    rw, rh = max(1, x2 - x1), max(1, y2 - y1)

    if probe is None:
        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    lo, hi = max(1, int(spec.min_size)), max(1, int(spec.max_size))
    if spec.size and spec.size > 0:
        # 固定字号
        size = max(1, int(spec.size))
        font = load_font(font_path, size)
        lines = wrap_lines(text, font, rw, probe,preserve_whitespace)
        _w, bh, lh = _measure(lines, font, probe, spec.line_spacing)
        if spec.auto_shrink and (bh > rh or _w > rw):
            # 自动缩小到能装下为止（二分，避免逐像素试探）
            lo2, hi2, best2 = 1, size - 1, 1
            while lo2 <= hi2:
                mid = (lo2 + hi2) // 2
                f2 = load_font(font_path, mid)
                ls2 = wrap_lines(text, f2, rw, probe,preserve_whitespace)
                w2, h2, lh2 = _measure(ls2, f2, probe, spec.line_spacing)
                if w2 <= rw and h2 <= rh:
                    best2 = mid
                    lo2 = mid + 1
                else:
                    hi2 = mid - 1
            size = max(1, best2)
            font = load_font(font_path, size)
            lines = wrap_lines(text, font, rw, probe,preserve_whitespace)
            _w, bh, lh = _measure(lines, font, probe, spec.line_spacing)
        best = TextBlock(lines, font, size, lh, bh, _w, y1)
    else:
        best_size, best_lines, best_lh, best_bh, best_w = 1, [text], 1, 1, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            f = load_font(font_path, mid)
            ls = wrap_lines(text, f, rw, probe,preserve_whitespace)
            w, h, lh = _measure(ls, f, probe, spec.line_spacing)
            if w <= rw and h <= rh:
                best_size, best_lines, best_lh, best_bh, best_w = mid, ls, lh, h, w
                lo = mid + 1
            else:
                hi = mid - 1
        font = load_font(font_path, best_size)
        if best_size == 1 and best_lines == [text]:
            best_lines = wrap_lines(text, font, rw, probe,preserve_whitespace)
            _w, best_bh, best_lh = _measure(best_lines, font, probe, spec.line_spacing)
            best_w = _w
        best = TextBlock(best_lines, font, best_size, best_lh, best_bh, best_w, y1)

    # 垂直对齐
    if spec.valign == "middle":
        best.y_start = y1 + max(0, (rh - best.block_height) // 2)
    elif spec.valign == "bottom":
        best.y_start = y2 - best.block_height
    else:
        best.y_start = y1
    return best


# --------------------------------------------------------------------------- #
# 绘制
# --------------------------------------------------------------------------- #
def _draw_styled(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    color: RGBA,
    stroke_mode: str,
    stroke_w: int,
    stroke_color: RGBA,
) -> None:
    """按指定描边模式绘制一段文字（不处理换行）。"""
    x, y = xy
    if stroke_mode == "outline" and stroke_w > 0:
        draw.text((x, y), text, font=font, fill=color,
                  stroke_width=int(stroke_w), stroke_fill=stroke_color)
    elif stroke_mode == "shadow" and stroke_w > 0:
        draw.text((x + stroke_w, y + stroke_w), text, font=font, fill=stroke_color)
        draw.text((x, y), text, font=font, fill=color)
    else:
        draw.text((x, y), text, font=font, fill=color)


def draw_body(
    target: Image.Image,
    block: TextBlock,
    region: tuple[int, int, int, int],
    spec: FontSpec,
    bracket_color: RGB | None = None,
    bracket_state: bool = False,
) -> None:
    """把排版好的正文画到 target 上。"""
    x1, _y1, x2, _y2 = region
    rw = max(1, x2 - x1)
    draw = ImageDraw.Draw(target)
    base = spec.resolved_color()
    stroke = spec.resolved_stroke_color()
    br = spec.resolved_bracket_color(bracket_color) if spec.bracket_highlight else base

    y = block.y_start
    in_bracket = bracket_state
    for ln in block.lines:
        line_w = int(draw.textlength(ln, font=block.font))
        if spec.align == "center":
            x = x1 + max(0, (rw - line_w) // 2)
        elif spec.align == "right":
            x = x2 - line_w
        else:
            x = x1

        if spec.bracket_highlight:
            segs, in_bracket = _segments(ln, in_bracket, base, br)
        else:
            segs = [(ln, base)] if ln else []

        for seg_text, seg_color in segs:
            if not seg_text:
                continue
            _draw_styled(draw, (x, y), seg_text, block.font, seg_color,
                         spec.stroke_mode, spec.stroke_width, stroke)
            x += int(draw.textlength(seg_text, font=block.font))
        y += block.line_height


def draw_name(
    target: Image.Image,
    name: str,
    origin: tuple[int, int],
    spec: FontSpec,
    font_path: str | None,
) -> int:
    """在 origin 处画角色名，返回其占用高度（含描边余量）。"""
    if not name:
        return 0
    font = load_font(font_path, max(1, int(spec.name_size)))
    draw = ImageDraw.Draw(target)

    x = origin[0] + int(spec.name_offset_x)
    y = origin[1] + int(spec.name_offset_y)
    if spec.name_align == "center":
        w = int(draw.textlength(name, font=font))
        x -= w // 2
    elif spec.name_align == "right":
        w = int(draw.textlength(name, font=font))
        x -= w

    _draw_styled(draw, (x, y), name, font,
                 spec.resolved_name_color(),
                 spec.name_stroke_mode, spec.name_stroke_width,
                 spec.resolved_name_stroke_color())

    ascent, descent = font.getmetrics()
    return ascent + descent + int(spec.name_stroke_width)


def name_height(name: str, spec: FontSpec, font_path: str | None) -> int:
    if not name:
        return 0
    font = load_font(font_path, max(1, int(spec.name_size)))
    ascent, descent = font.getmetrics()
    return ascent + descent + int(spec.name_stroke_width)


# --------------------------------------------------------------------------- #
# 自测
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import os

    from models import FontSpec as FS
    import settings

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    os.makedirs(out_dir, exist_ok=True)
    settings.ensure_local_fonts(settings.resolve_asset_root())
    choices = settings.font_choices()
    fp = choices[0][1] if choices else None
    print("使用字体:", fp)

    W, H = 900, 560
    canvas = Image.new("RGBA", (W, H), (40, 40, 54, 255))
    region = (30, 90, W - 30, H - 30)

    cases = [
        ("auto size / shadow / bracket", FS(size=0, max_size=64, stroke_mode="shadow", stroke_width=3)),
        ("fixed size 32 / outline 3", FS(size=32, max_size=64, stroke_mode="outline", stroke_width=3)),
        ("auto / no stroke / centered", FS(size=0, max_size=64, stroke_mode="none", stroke_width=0, align="center")),
        ("auto / outline 2 / right", FS(size=0, max_size=64, stroke_mode="outline", stroke_width=2, align="right")),
    ]

    text = ("这是自适应排版测试：二分搜索最大字号 + 自动换行。\n"
            "[括号内的文字] 会使用角色主色高亮，【方头括号也一样】。\n"
            "Mixed 中英文 and ASCII 12345 都应正确折行。")

    y = 8
    for label, spec in cases:
        probe = ImageDraw.Draw(canvas)
        probe.text((30, y + 30), label, fill=(200, 200, 220, 255))
        sub = (30, y + 56, W - 30, y + 128)
        block = layout_text(text, sub, spec, fp, bracket_color=(255, 170, 80), probe=probe)
        draw_body(canvas, block, sub, spec, bracket_color=(255, 170, 80))
        ImageDraw.Draw(canvas).text(
            (30, y + 130), f"-> 字号={block.font_size} 行数={len(block.lines)} 高={block.block_height}",
            fill=(150, 220, 160, 255))
        ImageDraw.Draw(canvas).rectangle(sub, outline=(90, 90, 120, 255))
        y += 140

    p = os.path.join(out_dir, "test_text.png")
    canvas.convert("RGB").save(p)
    print("saved:", p, canvas.size)

    # 角色名样式
    nm = Image.new("RGBA", (600, 240), (40, 40, 54, 255))
    d = ImageDraw.Draw(nm)
    styles = [
        ("shadow 2", FS(name_size=36, name_stroke_mode="shadow", name_stroke_width=2,
                        name_color="#ffd479")),
        ("outline 2", FS(name_size=36, name_stroke_mode="outline", name_stroke_width=2,
                         name_color="#ffffff")),
        ("none / big", FS(name_size=52, name_stroke_mode="none", name_stroke_width=0,
                          name_color="#7ee0ff")),
    ]
    yy = 30
    for label, spec in styles:
        h = draw_name(nm, f"橘雪莉 · {label}", (40, yy), spec, fp)
        yy += h + 24
    p2 = os.path.join(out_dir, "test_name.png")
    nm.convert("RGB").save(p2)
    print("saved:", p2, nm.size)
