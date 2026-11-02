"""One text allocation path for editor previews, exports and captured chat input."""
from dataclasses import dataclass, field, replace
import math
import re
import geometry
import project_store
import render_text


PUNCTUATION = '，。！？,.!?'
CLOSING = '\"\'”’)]}）】》〉」』'
_CLAUSE_END = re.compile('['+re.escape(PUNCTUATION)+']+['+re.escape(CLOSING)+r']*[ \t\n]*')


def split_clauses(text):
    """Keep punctuation runs, closing quotes and trailing spaces with their clause."""
    parts=[]; start=0
    for match in _CLAUSE_END.finditer(text):
        parts.append(text[start:match.end()]); start=match.end()
    if start<len(text): parts.append(text[start:])
    return parts


def trim_frame_punctuation(text):
    """Remove the frame's final punctuation run unless explicitly escaped.

    The source allocation and editor input remain untouched, including punctuation.
    Closing quotes/brackets and trailing spaces survive the cosmetic deletion.
    One ASCII space immediately before the final run protects its punctuation;
    that marker alone is hidden. Other whitespace is not an escape marker.
    """
    end=len(text.rstrip()); punctuation_end=end
    while punctuation_end and text[punctuation_end-1] in CLOSING:
        punctuation_end-=1
    start=punctuation_end
    while start and text[start-1] in PUNCTUATION:
        start-=1
    if start<punctuation_end and start and text[start-1]==' ':
        return text[:start-1]+text[start:]
    return text[:start]+text[punctuation_end:]


def frame_text(text):
    # Check the original whitespace before flattening line breaks for sequence
    # mode, otherwise a line break could become an accidental space marker.
    return trim_frame_punctuation(text).replace('\r\n','\n').replace('\r','\n').replace('\n',' ')


def frame_capacity(layer):
    """Usable CJK character area at the actual fixed size, spacing and margins."""
    region=text_region(layer)
    font=render_text.load_font(project_store.resolve_font(layer.font.family),layer.font.size)
    ascent,descent=font.getmetrics()
    line_height=max(1,int((ascent+descent)*(1+layer.font.line_spacing)))
    rows=max(0,(region[3]-region[1])//line_height)
    return rows*(region[2]-region[0])/max(1,font.getlength('国'))


def text_region(layer):
    margin=min(8+layer.font.stroke_width,max(0,(min(layer.width,layer.height)-1)//2))
    return (margin,margin,max(margin+1,layer.width-margin),max(margin+1,layer.height-margin))


def flow_layer(layer):
    # Measure unrotated edges so rotation does not change capacity. Perspective is
    # applied afterwards, and resizing the quad changes the amount of text it holds.
    width=(math.dist(layer.quad[0],layer.quad[1])+math.dist(layer.quad[2],layer.quad[3]))/2
    height=(math.dist(layer.quad[1],layer.quad[2])+math.dist(layer.quad[3],layer.quad[0]))/2
    font=layer.font.copy(); font.size=font.size or font.max_size; font.auto_shrink=False
    return replace(layer,width=max(1,min(8192,round(width))),height=max(1,min(8192,round(height))),font=font)


@dataclass
class TextFlow:
    messages: dict = field(default_factory=dict)
    layers: dict = field(default_factory=dict)
    parts: list = field(default_factory=list)
    overflow: str = ''
    bracket_states: dict = field(default_factory=dict)

    @property
    def error(self):
        if self.overflow:
            return f'为保持语段完整，仍有 {len(self.overflow)} 字未放入。请放大文字框、减小字号或增加合适的文字框。'
        return ''


def distribute(scene,message=None):
    text=scene.message if message is None else message
    flow=TextFlow()
    if scene.text_mode!='sequence':
        flow.messages={layer.id:trim_frame_punctuation(text) for layer in scene.layers if layer.kind=='text'}
        return flow
    remaining=text.replace('\r\n','\n').replace('\r','\n')
    if not remaining.strip(): return flow
    canvas=geometry.rectangle(0,0,scene.width,scene.height); groups={}
    for layer in scene.layers:
        if layer.kind=='text' and layer.visible and layer.opacity>0 and geometry.intersects(layer.quad,canvas):
            fitted=flow_layer(layer); flow.layers[layer.id]=fitted
            groups.setdefault(layer.text_index,[]).append(fitted)
    if not groups:
        flow.overflow=remaining; return flow
    ordered=[layers for _,layers in sorted(groups.items())]
    clauses=split_clauses(remaining); offsets=[0]
    for clause in clauses: offsets.append(offsets[-1]+len(clause))
    capacities=[min(frame_capacity(layer) for layer in layers) for layers in ordered]
    total_capacity=sum(capacities)
    if not total_capacity:
        flow.overflow=remaining; return flow

    # Global contiguous partitioning: first maximize the complete source prefix
    # placed, then minimize deviation from each frame's share of usable capacity.
    # Empty assignments allow an intact long clause to pass a small frame and use
    # a later larger one. Duplicate numbers use their smallest shared capacity.
    costs={0:0.0}; paths=[]; count=len(clauses)
    for layers,capacity in zip(ordered,capacities):
        target=len(remaining)*capacity/total_capacity
        next_costs={}; previous={}
        def fits(start,end):
            display=frame_text(remaining[offsets[start]:offsets[end]])
            return all(render_text.fit_text_prefix(display,text_region(layer),layer.font,
                       project_store.resolve_font(layer.font.family))==display for layer in layers)
        for start,cost in costs.items():
            low,high=start,count
            while low<high:
                middle=(low+high+1)//2
                if fits(start,middle): low=middle
                else: high=middle-1
            for end in range(start,low+1):
                size=offsets[end]-offsets[start]
                candidate=cost+(size-target)**2/max(1,target)
                if candidate<next_costs.get(end,float('inf')):
                    next_costs[end]=candidate; previous[end]=start
        costs=next_costs; paths.append(previous)
    consumed=max(costs)
    end=consumed; assignments=[]
    for previous in reversed(paths):
        start=previous[end]; assignments.append((start,end)); end=start
    assignments.reverse(); in_bracket=False
    for layers,(start,end) in zip(ordered,assignments):
        prefix=remaining[offsets[start]:offsets[end]]
        if prefix: flow.parts.append(prefix)
        for layer in layers:
            flow.messages[layer.id]=frame_text(prefix); flow.bracket_states[layer.id]=in_bracket
        for ch in prefix:
            if ch in '[【': in_bracket=True
            elif ch in ']】': in_bracket=False
    flow.overflow=remaining[offsets[consumed]:]
    return flow
