"""四维独立预设的数据模型 —— 头像 / 聊天背景图 / 边框(气泡) / 字体。

设计原则
--------
1. **四维正交**：四个维度各自独立存取、独立预设、自由组合，互不耦合。
2. **可序列化往返**：每个 spec 都能 `to_dict()` / `from_dict()`，未知字段被忽略，
   缺失字段用默认值补齐 —— 这样以后加字段不会让旧预设文件失效。
3. **纯数据**：本模块不 import PIL，不碰文件系统，方便单独测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, asdict
from typing import Any

__all__ = [
    "AvatarSpec", "BackgroundSpec", "BubbleSpec", "FontSpec",
    "LayoutSpec", "CanvasSpec", "InstantSpec", "Preset", "StudioConfig",
    "SPEC_REGISTRY", "Color",
]

Color = tuple[int, int, int]
RGBA = tuple[int, int, int, int]


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
def parse_color(value: Any, default: Any = None) -> Any:
    """接受 '#rrggbb' / '#rrggbbaa' / 'rrggbb' / [r,g,b] / [r,g,b,a] / None。"""
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        nums = [int(v) for v in value]
        if any(v<0 or v>255 for v in nums): raise ValueError('颜色分量必须在 0—255 之间')
        if len(nums) == 3:
            return (nums[0], nums[1], nums[2], 255)
        if len(nums) == 4:
            return (nums[0], nums[1], nums[2], nums[3])
        raise ValueError(f"颜色分量个数必须是 3 或 4：{value!r}")
    s = str(value).strip().lstrip("#")
    if len(s) == 3:  # #abc → #aabbcc
        s = "".join(c * 2 for c in s)
    if len(s) == 6:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), 255)
    if len(s) == 8:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), int(s[6:8], 16))
    raise ValueError(f"无法解析颜色：{value!r}")


def color_to_hex(c: RGBA) -> str:
    if c[3] >= 255:
        return "#%02x%02x%02x" % c[:3]
    return "#%02x%02x%02x%02x" % c


def _from_dict(cls, data: dict | None):
    """按 dataclass 字段过滤未知键，缺失键用默认值。"""
    data = data or {}
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


class _Base:
    """提供 to_dict / from_dict / copy / replace 的通用行为。"""

    def to_dict(self) -> dict:
        return asdict(self)  # type: ignore[arg-type]

    @classmethod
    def from_dict(cls, data: dict | None):
        return _from_dict(cls, data)

    def copy(self):
        return type(self).from_dict(self.to_dict())

    def replace(self, **kw):
        d = self.to_dict()
        d.update(kw)
        return type(self).from_dict(d)


# --------------------------------------------------------------------------- #
# 维度 1：头像
# --------------------------------------------------------------------------- #
@dataclass
class AvatarSpec(_Base):
    """头像。默认取自角色立绘的脸部裁切。

    注意 `enabled=False` 是刻意的默认值：默认版面里角色立绘已经占了左侧，
    再叠一个同角色头像会盖住立绘的脸。开启头像通常应该同时关掉立绘
    （LayoutSpec.draw_sprite=False），或把头像移到别处。
    """

    enabled: bool = False
    source: str = "sprite"          # sprite(角色立绘) | image(独立图片) | none
    image_path: str = ""            # source=image 时的文件路径
    # 裁切框（相对源图像素坐标）。全 None 时由 regions.py 自动推算。
    crop_left: int | None = None
    crop_top: int | None = None
    crop_right: int | None = None
    crop_bottom: int | None = None
    size: int = 150                 # 输出到画布上的边长（正方形）
    shape: str = "rounded"          # circle | rounded | square
    corner_radius: int = 24         # shape=rounded 时生效
    border_width: int = 0
    border_color: list | str = "#ffffff"
    # 位置：相对「文本框」左边缘的偏移；负数即位于文本框外侧左边
    offset_x: int = -190
    offset_y: int = 0
    valign: str = "top"             # top | middle | bottom（相对文本框高度对齐）
    opacity: int = 255
    flip_horizontal: bool = False   # 立绘默认朝右，做头像时常常需要镜像

    def resolved_color(self) -> RGBA:
        return parse_color(self.border_color, (255, 255, 255, 255))


# --------------------------------------------------------------------------- #
# 维度 2：聊天背景图
# --------------------------------------------------------------------------- #
@dataclass
class BackgroundSpec(_Base):
    """聊天背景图（画布底层）。"""

    source: str = "preset"          # preset(内置16张) | image(自定义) | solid(纯色)
    preset: str = "c1.png"          # assets/background 下的文件名
    image_path: str = ""            # source=image 时的文件路径
    color: list | str = "#1b1b26"   # source=solid 时的底色
    fit: str = "cover"              # cover | contain | stretch | tile
    brightness: float = 1.0         # 1.0 为原始；<1 变暗
    blur: float = 0.0               # 高斯模糊半径，0 为关闭
    dim: int = 0                    # 叠加一层黑色蒙版的 alpha(0-255)，让文字更清晰

    def resolved_color(self) -> RGBA:
        return parse_color(self.color, (27, 27, 38, 255))


# --------------------------------------------------------------------------- #
# 维度 3：边框（气泡）
# --------------------------------------------------------------------------- #
@dataclass
class BubbleSpec(_Base):
    """气泡本体：填充、圆角、描边、尾巴、内外边距。"""

    enabled: bool = True
    fill_color: list | str = "#ffffff"
    fill_opacity: int = 235         # 0-255，可透出背景
    corner_radius: int = 28
    border_width: int = 0
    border_color: list | str = "#d0d0d0"
    # 尾巴（小尖角）
    tail: str = "left"              # none | left | right | top | bottom
    tail_size: int = 26
    tail_offset: int = 44           # 尾巴沿所在边从起点算起的偏移
    padding_x: int = 40             # 文字与气泡内壁的左右间距
    padding_y: int = 28
    shadow_offset: int = 0          # 气泡投影偏移，0 为关闭
    shadow_color: list | str = "#000000"
    shadow_opacity: int = 90

    def fill_rgba(self, override_alpha: int | None = None) -> RGBA:
        c = parse_color(self.fill_color, (255, 255, 255, 255))
        a = self.fill_opacity if override_alpha is None else override_alpha
        return (c[0], c[1], c[2], max(0, min(255, a)))

    def border_rgba(self) -> RGBA:
        return parse_color(self.border_color, (208, 208, 208, 255))

    def shadow_rgba(self) -> RGBA:
        c = parse_color(self.shadow_color, (0, 0, 0, 255))
        return (c[0], c[1], c[2], max(0, min(255, self.shadow_opacity)))


# --------------------------------------------------------------------------- #
# 维度 4：字体
# --------------------------------------------------------------------------- #
@dataclass
class FontSpec(_Base):
    """字体。同时服务「角色名」与「正文」两块文字。"""

    # 正文字体
    family: str = "assets/fonts/font3.ttf"
    size: int = 0                   # 0 = 自动（沿用原项目的二分搜索最大字号）
    max_size: int = 145             # 自动模式下的字号上限
    min_size: int = 12
    color: list | str = "#ffffff"
    align: str = "left"             # left | center | right
    valign: str = "top"             # top | middle | bottom
    line_spacing: float = 0.15
    stroke_width: int = 4           # 阴影/描边宽度
    stroke_color: list | str = "#000000"
    stroke_mode: str = "shadow"     # shadow(偏移阴影，原项目行为) | outline(四周描边) | none
    # 固定字号超出区域时是否自动缩小到能装下（size=0 自动模式时无意义）
    auto_shrink: bool = True
    # 括号高亮（原项目特性，保留）
    bracket_highlight: bool = True
    bracket_color: list | str = ""  # 空 = 用角色主色。注意：深色气泡上角色主色
                                    # 可能几乎看不见（实测 B 变体就是这种情况），
                                    # 因此需要时请显式指定一个对比色。

    # 角色名
    name_family: str = "assets/fonts/font3.ttf"
    name_size: int = 44
    name_color: list | str = "#ffffff"
    name_stroke_width: int = 2
    name_stroke_color: list | str = "#000000"
    name_stroke_mode: str = "shadow"
    name_align: str = "left"
    name_offset_x: int = 0          # 相对气泡内容区左上角
    name_offset_y: int = 0
    name_gap: int = 12              # 名字与正文之间的垂直间距

    def resolved_color(self) -> RGBA:
        return parse_color(self.color, (255, 255, 255, 255))

    def resolved_stroke_color(self) -> RGBA:
        return parse_color(self.stroke_color, (0, 0, 0, 255))

    def resolved_name_color(self) -> RGBA:
        return parse_color(self.name_color, (255, 255, 255, 255))

    def resolved_name_stroke_color(self) -> RGBA:
        return parse_color(self.name_stroke_color, (0, 0, 0, 255))

    def resolved_bracket_color(self, fallback: Any = None) -> RGBA:
        if self.bracket_color not in ("", None, []):
            return parse_color(self.bracket_color, (255, 255, 255, 255))
        return parse_color(fallback, (255, 255, 255, 255))


# --------------------------------------------------------------------------- #
# 版面：文本框矩形 / 画布
# --------------------------------------------------------------------------- #
@dataclass
class LayoutSpec(_Base):
    """文本框矩形（所有坐标基于未压缩的原始画布）。"""

    left: int = 728
    top: int = 355
    right: int = 2339
    bottom: int = 800
    # 绘制角色立绘（保留原项目的立绘层）
    draw_sprite: bool = True
    sprite_offset_x: int = 0
    sprite_offset_y: int = 134

    def rect(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)

    def width(self) -> int:
        return self.right - self.left

    def height(self) -> int:
        return self.bottom - self.top


@dataclass
class CanvasSpec(_Base):
    """输出画布与压缩。"""

    width: int = 2560
    height: int = 834
    # 压缩到目标宽度；0 或 None 表示不压缩（保留原始尺寸）
    output_width: int | None = 1200
    output_format: str = "png"      # png | jpeg | webp
    jpeg_quality: int = 92
    # 输出宽高均不超过该上限（None 表示不限制）
    max_output_height: int | None = 800

    def target_size(self) -> tuple[int, int] | None:
        """算出压缩后的尺寸；None 表示不压缩。"""
        if not self.output_width:
            return None
        ratio = self.output_width / self.width
        w = int(self.width * ratio)
        h = int(self.height * ratio)
        if self.max_output_height and h > self.max_output_height:
            r2 = self.max_output_height / h
            w, h = int(w * r2), int(h * r2)
        return (max(1, w), max(1, h))


# --------------------------------------------------------------------------- #
# 瞬发工作流（上游 Ctrl+E 那套流程的可选模式）
# --------------------------------------------------------------------------- #
@dataclass
class InstantSpec(_Base):
    """瞬发配置：全局热键 → 抓取输入框文字 → 生成 → 粘贴 → 发送。"""

    enabled: bool = False
    hotkey: str = "ctrl+e"
    whitelist: list = field(default_factory=lambda: [
        "tim.exe", "qq.exe", "wechat.exe", "wechatapp.exe", "weixin.exe",
    ])
    enforce_whitelist: bool = True
    select_all_keys: str = "ctrl+a"
    cut_keys: str = "ctrl+x"
    paste_keys: str = "ctrl+v"
    send_keys: str = "enter"
    delay: float = 0.12
    send_delay: float = 0.35
    auto_paste: bool = True
    auto_send: bool = True
    # 剪贴板图片的可读文件名。留空 = 用默认模板。
    # 支持 {name} {character} {date} {time} 占位符。
    # 之所以要这个：聊天软件从剪贴板只拿到裸图，会自己编号成一长串数字；
    # 同时放一个带名字的 CF_HDROP 文件列表，它们就有机会显示这个名字。
    clipboard_name: str = "魔裁文本框_{name}_{date}_{time}"

    def whitelist_set(self) -> set[str]:
        """归一化后的进程名集合（小写、去空白），用于前台进程校验。"""
        return {str(n).strip().lower() for n in (self.whitelist or []) if str(n).strip()}


# --------------------------------------------------------------------------- #
# 一个完整预设 = 四维 + 版面 + 瞬发
# --------------------------------------------------------------------------- #
@dataclass
class Preset(_Base):
    """一套完整外观配置。内含四个独立维度的 spec。"""

    name: str = "默认"
    avatar: AvatarSpec = field(default_factory=AvatarSpec)
    background: BackgroundSpec = field(default_factory=BackgroundSpec)
    bubble: BubbleSpec = field(default_factory=BubbleSpec)
    font: FontSpec = field(default_factory=FontSpec)
    layout: LayoutSpec = field(default_factory=LayoutSpec)
    canvas: CanvasSpec = field(default_factory=CanvasSpec)
    instant: InstantSpec = field(default_factory=InstantSpec)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "avatar": self.avatar.to_dict(),
            "background": self.background.to_dict(),
            "bubble": self.bubble.to_dict(),
            "font": self.font.to_dict(),
            "layout": self.layout.to_dict(),
            "canvas": self.canvas.to_dict(),
            "instant": self.instant.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict | None):
        data = data or {}
        return cls(
            name=data.get("name", "默认"),
            avatar=AvatarSpec.from_dict(data.get("avatar")),
            background=BackgroundSpec.from_dict(data.get("background")),
            bubble=BubbleSpec.from_dict(data.get("bubble")),
            font=FontSpec.from_dict(data.get("font")),
            layout=LayoutSpec.from_dict(data.get("layout")),
            canvas=CanvasSpec.from_dict(data.get("canvas")),
            instant=InstantSpec.from_dict(data.get("instant")),
        )

    def dimension(self, key: str):
        """按名字取某个维度，用于四维独立的下拉选择。"""
        return SPEC_REGISTRY[key][0](**getattr(self, key).to_dict())


# 维度注册表：key → (spec 类, 中文显示名)
SPEC_REGISTRY: dict[str, tuple[type, str]] = {
    "avatar": (AvatarSpec, "头像"),
    "background": (BackgroundSpec, "聊天背景图"),
    "bubble": (BubbleSpec, "边框 / 气泡"),
    "font": (FontSpec, "字体"),
}


# --------------------------------------------------------------------------- #
# 应用级配置（预设库 + 当前选择）
# --------------------------------------------------------------------------- #
@dataclass
class StudioConfig(_Base):
    """软件状态：四套独立的预设库 + 当前各维度选中的预设 + 当前完整配置。"""

    current: Preset = field(default_factory=Preset)
    presets: dict = field(default_factory=dict)   # 维度名 → {预设名: spec dict}
    last_text: str = ""
    last_character: str = "sherri"
    last_emotion: int = 1
    window_geometry: str = ""

    def preset_names(self, dim: str) -> list[str]:
        return sorted(self.presets.get(dim, {}).keys())

    def get_preset(self, dim: str, name: str):
        data = self.presets.get(dim, {}).get(name)
        if data is None:
            return None
        return SPEC_REGISTRY[dim][0].from_dict(data)

    def save_preset(self, dim: str, name: str, spec) -> None:
        self.presets.setdefault(dim, {})[name] = spec.to_dict()

    def delete_preset(self, dim: str, name: str) -> bool:
        return self.presets.get(dim, {}).pop(name, None) is not None

    def apply_preset(self, dim: str, name: str) -> bool:
        """把某个维度的预设内容套到当前配置上（只覆盖该维度）。"""
        spec = self.get_preset(dim, name)
        if spec is None:
            return False
        setattr(self.current, dim, spec)
        return True

    def to_dict(self) -> dict:
        return {
            "current": self.current.to_dict(),
            "presets": self.presets,
            "last_text": self.last_text,
            "last_character": self.last_character,
            "last_emotion": self.last_emotion,
            "window_geometry": self.window_geometry,
        }

    @classmethod
    def from_dict(cls, data: dict | None):
        data = data or {}
        presets = data.get("presets") or {}
        # 只保留认识的四个维度，防止旧版本脏数据
        clean = {k: v for k, v in presets.items() if k in SPEC_REGISTRY and isinstance(v, dict)}
        return cls(
            current=Preset.from_dict(data.get("current")),
            presets=clean,
            last_text=data.get("last_text", "") or "",
            last_character=data.get("last_character", "sherri") or "sherri",
            last_emotion=int(data.get("last_emotion", 1) or 1),
            window_geometry=data.get("window_geometry", "") or "",
        )


# --------------------------------------------------------------------------- #
# 自测
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json

    print("=== 颜色解析 ===")
    for v in ["#ff8800", "ff8800", "#f80", "#ff880080", [1, 2, 3], [1, 2, 3, 4]]:
        c = parse_color(v)
        print(f"  {v!r:20} -> {c}  -> {color_to_hex(c)}")

    print("\n=== 预设往返 ===")
    p = Preset(name="测试预设")
    p.avatar.shape = "circle"
    p.avatar.size = 180
    p.bubble.tail = "right"
    p.bubble.fill_color = "#4a7cff"
    p.font.size = 0
    d = p.to_dict()
    p2 = Preset.from_dict(d)
    print("  往返相等:", p2.to_dict() == d)

    print("\n=== 未知字段被忽略 / 缺失字段补默认 ===")
    noisy = {"name": "x", "avatar": {"size": 99, "未来字段": 1}, "bubble": None}
    p3 = Preset.from_dict(noisy)
    print("  avatar.size =", p3.avatar.size, "| bubble.fill_opacity =", p3.bubble.fill_opacity,
          "| 有'未来字段'?", hasattr(p3.avatar, "未来字段"))

    print("\n=== 四维注册表 ===")
    for k, (cls, label) in SPEC_REGISTRY.items():
        print(f"  {k:12} {label:12} {cls.__name__}  字段数={len(fields(cls))}")

    print("\n=== StudioConfig：四维独立预设 ===")
    cfg = StudioConfig()
    cfg.save_preset("bubble", "微信绿", BubbleSpec(fill_color="#95ec69", corner_radius=8, tail="left"))
    cfg.save_preset("bubble", "游戏原生", BubbleSpec(fill_color="#1e2233", fill_opacity=200, corner_radius=28))
    cfg.save_preset("avatar", "雪莉圆头像", AvatarSpec(shape="circle", size=150))
    cfg.save_preset("font", "白字黑边", FontSpec(color="#ffffff", stroke_mode="outline", stroke_width=3))
    cfg.save_preset("background", "牢房", BackgroundSpec(preset="c1.png", dim=40))
    print("  各维度预设库:")
    for dim in SPEC_REGISTRY:
        print(f"    {dim:12} -> {cfg.preset_names(dim)}")

    print("\n  套用『微信绿』后 bubble.fill_color =", end=" ")
    cfg.apply_preset("bubble", "微信绿")
    print(cfg.current.bubble.fill_color, "| corner_radius =", cfg.current.bubble.corner_radius)

    print("\n=== 缓存尺寸计算 ===")
    for w, mh in [(1200, 800), (1200, None), (None, None), (2560, 900), (600, 800)]:
        c = CanvasSpec(output_width=w, max_output_height=mh)
        print(f"  output_width={w}, max_h={mh} -> {c.target_size()}")
