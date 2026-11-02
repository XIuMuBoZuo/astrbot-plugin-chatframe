"""Versioned scene data: shared or numbered text, with text/image layers."""
from __future__ import annotations
from dataclasses import dataclass, field
import math
import uuid
import geometry
from models import FontSpec, InstantSpec, parse_color, color_to_hex

SCHEMA_VERSION = 4
SUPPORTED_VERSIONS = (2,3,4)

def _integer(value, low, high, name):
    if isinstance(value, bool): raise ValueError(f'{name}不是有效数字')
    try: number = float(value)
    except (TypeError, ValueError): raise ValueError(f'{name}不是有效数字') from None
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f'{name}必须在 {low}—{high} 之间')
    return int(number)

def default_font():
    return FontSpec(size=0, min_size=8, max_size=120, color='#ffffff',
                    stroke_mode='none', stroke_width=0, line_spacing=0.15,
                    valign='middle', bracket_color='#ffcc80')

@dataclass
class Layer:
    kind: str = 'text'
    name: str = '文字框'
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    visible: bool = True
    locked: bool = False
    opacity: int = 255
    quad: list = field(default_factory=lambda: geometry.rectangle(80, 24, 1440, 220))
    width: int = 1440
    height: int = 220
    font: FontSpec = field(default_factory=default_font)
    image_path: str = ''
    crop: tuple | None = None
    text_index: int = 1

    def to_dict(self):
        return dict(kind=self.kind, name=self.name, id=self.id, visible=self.visible,
                    locked=self.locked, opacity=self.opacity, quad=[list(p) for p in self.quad],
                    width=self.width, height=self.height, font=self.font.to_dict(),
                    image_path=self.image_path, crop=list(self.crop) if self.crop else None,
                    text_index=self.text_index)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or data.get('kind', 'text') not in ('text', 'image'):
            raise ValueError('图层类型无效')
        quad = [tuple(map(float, p)) for p in data.get('quad', geometry.rectangle(80, 24, 1440, 220))]
        if not geometry.valid_quad(quad) or any(abs(v)>100000 for p in quad for v in p):
            raise ValueError('图层四角位置无效')
        crop = data.get('crop')
        if crop is not None:
            if len(crop) != 4: raise ValueError('裁剪范围无效')
            crop = tuple(_integer(v, 0, 100000, '裁剪坐标') for v in crop)
            if crop[2] <= crop[0] or crop[3] <= crop[1]: raise ValueError('裁剪区域不能为空')
        font = FontSpec.from_dict(data.get('font') or default_font().to_dict())
        font.size = _integer(font.size, 0, 2000, '字号')
        font.min_size = _integer(font.min_size, 1, 2000, '最小字号')
        font.max_size = _integer(font.max_size, 1, 2000, '最大字号')
        font.stroke_width = _integer(font.stroke_width, 0, 100, '描边宽度')
        font.line_spacing=float(font.line_spacing)
        if not math.isfinite(font.line_spacing) or not -0.5 <= font.line_spacing <= 4:
            raise ValueError('行距无效')
        for key in ('color','stroke_color'):
            parsed=parse_color(getattr(font,key))
            if parsed is None: raise ValueError('文字颜色无效')
            setattr(font,key,color_to_hex(parsed))
        if font.bracket_color: font.bracket_color=color_to_hex(parse_color(font.bracket_color))
        if font.align not in ('left','center','right') or font.valign not in ('top','middle','bottom'):
            raise ValueError('文字对齐方式无效')
        if font.stroke_mode not in ('none','outline','shadow'): raise ValueError('描边方式无效')
        if not isinstance(font.family,str): raise ValueError('字体路径无效')
        text_index=_integer(data.get('text_index',1),1,200,'分镜编号')
        if float(data.get('text_index',1))!=text_index: raise ValueError('分镜编号必须为整数')
        return cls(kind=data.get('kind', 'text'), name=str(data.get('name', '图层')),
                   id=str(data.get('id') or uuid.uuid4().hex), visible=bool(data.get('visible', True)),
                   locked=bool(data.get('locked', False)), opacity=_integer(data.get('opacity',255),0,255,'透明度'),
                   quad=quad, width=_integer(data.get('width',1440),1,8192,'排版宽度'),
                   height=_integer(data.get('height',220),1,8192,'排版高度'),
                   font=font, image_path=str(data.get('image_path','')), crop=crop,
                   text_index=text_index)

    def copy(self):
        return Layer.from_dict(self.to_dict())

@dataclass
class Scene:
    width: int = 1600
    height: int = 480
    output_width: int = 1600
    background: str = '#000000'
    message: str = ''
    layers: list[Layer] = field(default_factory=list)
    text_mode: str = 'repeat'
    text_split: str = 'punctuation'

    def __post_init__(self):
        if self.text_split in ('auto','paragraph','line'):
            self.text_split='punctuation'

    def to_dict(self):
        return dict(version=SCHEMA_VERSION, width=self.width, height=self.height,
                    output_width=self.output_width, background=self.background,
                    message=self.message, layers=[layer.to_dict() for layer in self.layers],
                    text_mode=self.text_mode,text_split='punctuation')

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or data.get('version',SCHEMA_VERSION) not in SUPPORTED_VERSIONS:
            raise ValueError('模板版本无法识别')
        layers=[]; text_ordinal=0
        for source in data.get('layers',[]):
            layer=Layer.from_dict(source)
            if layer.kind=='text':
                text_ordinal+=1
                if 'text_index' not in source: layer.text_index=text_ordinal
            layers.append(layer)
        if len(layers) > 200: raise ValueError('一个模板最多支持 200 个图层')
        if len({l.id for l in layers}) != len(layers): raise ValueError('图层编号重复')
        background=parse_color(data.get('background','#000000'))
        if background is None: raise ValueError('背景颜色无效')
        mode=data.get('text_mode','repeat'); split=data.get('text_split','punctuation')
        if mode not in ('repeat','sequence'): raise ValueError('文字显示模式无效')
        if split not in ('punctuation','auto','paragraph','line'): raise ValueError('文字分段方式无效')
        scene=cls(width=_integer(data.get('width',1600),64,8192,'画布宽度'),
                   height=_integer(data.get('height',480),64,8192,'画布高度'),
                   output_width=_integer(data.get('output_width',data.get('width',1600)),64,8192,'输出宽度'),
                   background=color_to_hex(background), message=str(data.get('message','')),
                   layers=layers,text_mode=mode,text_split='punctuation')
        if scene.output_width**2*scene.height/scene.width>32_000_000:
            raise ValueError('输出图片过大，请降低输出宽度（最多 3200 万像素）')
        return scene

    def copy(self): return Scene.from_dict(self.to_dict())

    def text_layer(self):
        w, h = max(64,int(self.width*0.85)), max(48,int(self.height*0.48))
        return Layer(name=f'文字框 {1+sum(l.kind=="text" for l in self.layers)}',
                     quad=geometry.rectangle((self.width-w)/2,24,w,h), width=w,height=h,
                     text_index=self.next_text_index())

    def next_text_index(self):
        used={layer.text_index for layer in self.layers if layer.kind=='text'}
        candidate=max(used,default=0)+1
        if candidate<=200: return candidate
        unused=next((i for i in range(1,201) if i not in used),None)
        if unused is None: raise ValueError('分镜编号已用完（最多 200 个）')
        return unused

    def text_parts(self,message=None):
        if self.text_mode=='sequence':
            from text_flow import distribute
            return distribute(self,message).parts
        text=self.message if message is None else message
        return [text] if text.strip() else []

    def layer_message(self,layer,message=None,parts=None):
        from text_flow import distribute
        return distribute(self,message).messages.get(layer.id,'')

    def find(self, layer_id):
        return next((layer for layer in self.layers if layer.id == layer_id), None)

@dataclass
class Workspace:
    scene: Scene = field(default_factory=Scene)
    instant: InstantSpec = field(default_factory=InstantSpec)
    geometry: str = '1380x860'
    orb_position: list = field(default_factory=lambda: [80,80])
    image_folder: str = ''

    def to_dict(self):
        return dict(version=SCHEMA_VERSION,scene=self.scene.to_dict(),instant=self.instant.to_dict(),
                    geometry=self.geometry,orb_position=self.orb_position,image_folder=self.image_folder)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data,dict): raise ValueError('工作区数据无效')
        if data.get('version') not in SUPPORTED_VERSIONS: raise ValueError('工作区版本无法识别')
        spec = InstantSpec.from_dict(data.get('instant'))
        delay,send_delay=float(spec.delay),float(spec.send_delay)
        if not math.isfinite(delay) or not math.isfinite(send_delay): raise ValueError('瞬发延迟无效')
        spec.delay = max(0.02,min(3,delay))
        spec.send_delay = max(0.1,min(5,send_delay))
        spec.whitelist = sorted(spec.whitelist_set())
        pos = data.get('orb_position',[80,80])
        if not isinstance(pos,(list,tuple)) or len(pos)!=2: raise ValueError('浮球位置无效')
        return cls(Scene.from_dict(data['scene']),spec,str(data.get('geometry','1380x860')),
                   [_integer(v,-100000,100000,'浮球位置') for v in pos[:2]],str(data.get('image_folder','')))

class History:
    def __init__(self): self.past=[]; self.future=[]
    def push(self, scene):
        self.past.append(scene.to_dict()); self.past=self.past[-50:]; self.future.clear()
    def undo(self, scene):
        if not self.past: return scene
        self.future.append(scene.to_dict()); return Scene.from_dict(self.past.pop())
    def redo(self, scene):
        if not self.future: return scene
        self.past.append(scene.to_dict()); return Scene.from_dict(self.future.pop())
