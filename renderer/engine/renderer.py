"""Pure scene renderer. Editor guides are deliberately outside this pipeline."""
from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass, field, replace
import io
import math
import threading
from PIL import Image, ImageDraw, ImageOps
import geometry
import project_store
import render_text
import text_flow
from models import parse_color
from scene import Scene, Layer

class ImageCache:
    def __init__(self, capacity=8, max_bytes=256_000_000):
        self.capacity=capacity; self.max_bytes=max_bytes
        self._items=OrderedDict(); self._lock=threading.RLock()
    def get(self, value):
        path=project_store.resolve_path(value); stat=path.stat()
        key=(str(path.resolve()),stat.st_mtime_ns,stat.st_size)
        with self._lock:
            if key not in self._items:
                with Image.open(path) as im:
                    self._items[key]=ImageOps.exif_transpose(im).convert('RGBA')
                while len(self._items)>1 and (len(self._items)>self.capacity or
                         sum(im.width*im.height*4 for im in self._items.values())>self.max_bytes):
                    self._items.popitem(last=False)
            self._items.move_to_end(key)
            return self._items[key]
    def clear(self):
        with self._lock: self._items.clear()

IMAGE_CACHE=ImageCache()

@dataclass
class RenderResult:
    image: Image.Image
    raw_size: tuple[int,int]
    out_size: tuple[int,int]
    notes: list[str] = field(default_factory=list)
    png_bytes: bytes | None = None
    text_overflow: str = ''
    flow_error: str = ''
    text_frame_count: int = 0

def normalized_crop(crop, size):
    w,h=size
    if crop is None: return (0,0,w,h)
    l,t,r,b=map(int,crop)
    l=max(0,min(w-1,l)); t=max(0,min(h-1,t))
    return l,t,max(l+1,min(w,r)),max(t+1,min(h,b))

def _text_surface(layer, message, factor, preserve_whitespace=False, bracket_state=False):
    w=max(1,round(layer.width*factor)); h=max(1,round(layer.height*factor))
    surface=Image.new('RGBA',(w,h),(0,0,0,0))
    if not message.strip(): return surface
    # Lay out in scene coordinates so zoom and export resolution cannot change wrapping.
    # The layout probe is tiny; only the glyph surface uses the bounded quality factor.
    spec=layer.font.copy()
    base_region=text_flow.text_region(layer)
    font_path=project_store.resolve_font(spec.family)
    block=render_text.layout_text(message,base_region,spec,font_path,preserve_whitespace=preserve_whitespace)
    region=tuple(round(value*factor) for value in base_region)
    if factor!=1:
        font_size=max(1,round(block.font_size*factor))
        block=replace(block,font=render_text.load_font(font_path,font_size),font_size=font_size,
                      line_height=max(1,round(block.line_height*factor)),
                      block_height=round(block.block_height*factor),block_width=round(block.block_width*factor),
                      y_start=round(block.y_start*factor))
        spec.stroke_width=max(1,round(spec.stroke_width*factor)) if spec.stroke_width else 0
    render_text.draw_body(surface,block,region,spec,bracket_color=(255,204,128),bracket_state=bracket_state)
    return surface

def warp_surface(surface, quad, canvas_size):
    """Warp only the clipped destination bounds, rather than allocating a full layer canvas."""
    l,t,r,b=geometry.bounds(quad)
    l=max(0,math.floor(l)); t=max(0,math.floor(t))
    r=min(canvas_size[0],math.ceil(r)); b=min(canvas_size[1],math.ceil(b))
    if r<=l or b<=t: return None,(l,t)
    local=geometry.translate(quad,-l,-t)
    coeff=geometry.perspective_coefficients(local,surface.width,surface.height)
    warped=surface.transform((r-l,b-t),Image.Transform.PERSPECTIVE,coeff,
                             resample=Image.Resampling.BICUBIC)
    return warped,(l,t)

def render(scene: Scene, message=None, preview_size=None, output_width=None):
    width=output_width or scene.output_width
    factor=width/scene.width
    if preview_size:
        factor=min(preview_size[0]/scene.width,preview_size[1]/scene.height,1.5,
                   math.sqrt(4_000_000/(scene.width*scene.height)))
    size=(max(1,round(scene.width*factor)),max(1,round(scene.height*factor)))
    if size[0]*size[1]>32_000_000: raise ValueError('输出图片过大，请降低输出宽度（最多 3200 万像素）')
    canvas=Image.new('RGBA',size,parse_color(scene.background))
    notes=[]; flow=text_flow.distribute(scene,message)
    for layer in scene.layers:
        if not layer.visible or layer.opacity==0: continue
        quad=[(x*factor,y*factor) for x,y in layer.quad]
        l,t,r,b=geometry.bounds(quad)
        if r<0 or b<0 or l>=size[0] or t>=size[1]: continue
        if layer.kind=='text':
            text_layer=flow.layers.get(layer.id,layer)
            # Allocate text according to its screen footprint; final export retains original detail.
            edge=max(math.dist(quad[i],quad[(i+1)%4]) for i in range(4))
            quality=min(2.0,max(factor,edge/max(text_layer.width,text_layer.height)),
                        math.sqrt(16_000_000/(text_layer.width*text_layer.height)))
            surface=_text_surface(text_layer,flow.messages.get(layer.id,''),quality,
                                  preserve_whitespace=scene.text_mode=='sequence',
                                  bracket_state=flow.bracket_states.get(layer.id,False))
        else:
            original=IMAGE_CACHE.get(layer.image_path)
            surface=original.crop(normalized_crop(layer.crop,original.size))
            # Images need no more source pixels than their projected bounds in preview/export.
            max_edge=max(1,math.ceil(max(r-l,b-t)*1.3))
            if max(surface.size)>max_edge:
                surface.thumbnail((max_edge,max_edge),Image.Resampling.LANCZOS)
        patch,offset=warp_surface(surface,quad,size)
        if patch is None: continue
        if layer.opacity<255:
            patch.putalpha(patch.getchannel('A').point(lambda v: v*layer.opacity//255))
        canvas.alpha_composite(patch,offset)
        notes.append(layer.name)
    if flow.error: notes.append(flow.error)
    return RenderResult(canvas,(scene.width,scene.height),size,notes,
                        text_overflow=flow.overflow,flow_error=flow.error,text_frame_count=len(flow.parts))

def encode(result, format='png',quality=92):
    if result.text_overflow: raise ValueError(result.flow_error)
    fmt=format.lower(); buf=io.BytesIO()
    if fmt in ('jpg','jpeg'):
        result.image.convert('RGB').save(buf,format='JPEG',quality=quality,optimize=True)
    elif fmt=='webp':
        result.image.save(buf,format='WEBP',quality=quality,method=4)
    elif fmt=='png': result.image.save(buf,format='PNG',optimize=True)
    else: raise ValueError('不支持的图片格式：'+format)
    result.png_bytes=buf.getvalue()
    return result.png_bytes

def render_to_bytes(scene, message=None, format='png',**kwargs):
    result=render(scene,message,**kwargs)
    return encode(result,format),result

def render_to_file(scene,path,message=None):
    from pathlib import Path
    p=Path(path); fmt=p.suffix.lower().lstrip('.') or 'png'
    data,result=render_to_bytes(scene,message,fmt)
    p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(data)
    return result
