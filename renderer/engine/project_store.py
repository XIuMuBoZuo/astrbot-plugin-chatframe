"""Atomic workspace saving and portable templates with embedded original assets."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid
import zipfile
import time
import hashlib
from scene import Scene, Workspace
import runtime_paths

BUNDLE_DIR = runtime_paths.resource_dir()
APP_DIR = runtime_paths.data_dir()
WORKSPACE_PATH = APP_DIR / 'studio_workspace.json'
IMPORT_DIR = APP_DIR / 'assets' / 'imported'
FONT_DIR = APP_DIR / 'assets' / 'fonts'
OUTPUT_DIR = APP_DIR / 'output'
CLIP_DIR = OUTPUT_DIR / '_clip_temp_v2'

def resolve_path(value):
    p=Path(value)
    if p.is_absolute(): return p
    candidate=APP_DIR/p
    bundled=BUNDLE_DIR/p
    return bundled if not candidate.exists() and bundled.is_file() else candidate

def resolve_font(value):
    bundled_fonts=BUNDLE_DIR/'assets'/'fonts'
    for p in (resolve_path(value or ''),FONT_DIR/Path(value or '').name,
              bundled_fonts/Path(value or '').name,FONT_DIR/'font3.ttf',bundled_fonts/'font3.ttf'):
        if p.is_file(): return str(p)
    return None

def font_choices():
    labels={'font1.ttf':'Noto 黑体','font2.ttf':'Noto 宋体','font3.ttf':'Noto 宋体粗体'}
    choices=[]
    candidates={p.name:p for p in (BUNDLE_DIR/'assets'/'fonts').glob('*')}
    candidates.update({p.name:p for p in FONT_DIR.glob('*')})
    for p in sorted(candidates.values()):
        if p.suffix.lower() not in ('.ttf','.otf','.ttc'): continue
        label=labels.get(p.name,p.stem)
        if p.name.startswith('template_'):
            try:
                from PIL import ImageFont
                family,style=ImageFont.truetype(str(p),12).getname()
                label=family+' · '+style
            except OSError: continue
        if any(name==label for name,_ in choices): label+=' ('+p.stem[-6:]+')'
        choices.append((label,str(p)))
    return choices

def import_image(path):
    source=Path(path).resolve()
    IMPORT_DIR.mkdir(parents=True,exist_ok=True)
    dest=IMPORT_DIR/(uuid.uuid4().hex+source.suffix.lower())
    shutil.copy2(source,dest)
    return dest.relative_to(APP_DIR).as_posix()

def _atomic_file(path, writer):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'_',dir=path.parent)
    os.close(fd)
    try:
        writer(Path(tmp))
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.remove(tmp)

def save_workspace(workspace, path=WORKSPACE_PATH):
    payload=json.dumps(workspace.to_dict(),ensure_ascii=False,indent=2,allow_nan=False)
    path=Path(path)
    if path.is_file():
        try:
            old=json.loads(path.read_text(encoding='utf-8'))
            Workspace.from_dict(old)
            _atomic_file(str(path)+'.bak',lambda tmp: shutil.copy2(path,tmp))
        except (ValueError,TypeError,KeyError):
            damaged=path.with_name(path.name+'.broken_'+uuid.uuid4().hex[:8])
            shutil.copy2(path,damaged)
    _atomic_file(path,lambda tmp: tmp.write_text(payload,encoding='utf-8'))

def load_workspace(path=WORKSPACE_PATH):
    path=Path(path)
    if not path.exists() and not Path(str(path)+'.bak').exists(): return Workspace(),''
    errors=[]
    for candidate in (path,Path(str(path)+'.bak')):
        try:
            workspace=Workspace.from_dict(json.loads(candidate.read_text(encoding='utf-8')))
            return workspace,('工作区读取失败，已恢复上次备份：'+errors[0] if errors else '')
        except (OSError,ValueError,TypeError,KeyError,OverflowError) as exc:
            errors.append(str(exc))
    return Workspace(),'工作区读取失败，原文件已保留：'+errors[0]

def prune_clip_files(protected=(),max_age_days=7):
    """Remove only old app-owned PNGs, retaining current clipboard references."""
    protected={Path(p).resolve() for p in protected if p}
    cutoff=time.time()-max_age_days*86400
    for path in CLIP_DIR.glob('转图_*.png'):
        try:
            if path.resolve() not in protected and path.stat().st_mtime<cutoff: path.unlink()
        except OSError: pass

def save_template(scene, path):
    data=scene.to_dict(); resources={}; fonts={}
    for layer in data['layers']:
        if layer['kind']=='image':
            p=resolve_path(layer['image_path'])
            if not p.is_file(): raise FileNotFoundError('素材缺失：'+str(p))
            arc='images/'+layer['id']+p.suffix.lower()
            resources[arc]=p; layer['image_path']=arc
        else:
            p=resolve_font(layer['font']['family'])
            if not p: raise FileNotFoundError('未找到文字框使用的字体')
            source=Path(p).resolve()
            arc=fonts.setdefault(source,'fonts/'+uuid.uuid4().hex+source.suffix.lower())
            resources[arc]=Path(p); layer['font']['family']=arc
    def write(tmp):
        with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('scene.json',json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False))
            for arc,p in resources.items(): z.write(p,arc)
    _atomic_file(path,write)

def load_template(path):
    installed=[]
    try:
        with zipfile.ZipFile(path) as z:
            data=json.loads(z.read('scene.json').decode('utf-8'))
            # Validate before installing any archive resource; do not extract archive paths.
            Scene.from_dict(data)
            extracted={}
            for layer in data['layers']:
                if layer['kind']=='image':
                    arc=layer['image_path']; folder=IMPORT_DIR; key='image_path'
                else:
                    arc=layer['font']['family']; folder=FONT_DIR; key=None
                if arc not in extracted:
                    folder.mkdir(parents=True,exist_ok=True)
                    payload=z.read(arc)
                    if key:
                        import io
                        from PIL import Image
                        with Image.open(io.BytesIO(payload)) as im: im.verify()
                    else:
                        import io
                        from PIL import ImageFont
                        ImageFont.truetype(io.BytesIO(payload),12)
                    dest=folder/('template_'+hashlib.sha256(payload).hexdigest()+Path(arc).suffix.lower())
                    if not dest.exists() or dest.read_bytes()!=payload:
                        _atomic_file(dest,lambda tmp:tmp.write_bytes(payload)); installed.append(dest)
                    extracted[arc]=dest.relative_to(APP_DIR).as_posix()
                value=extracted[arc]
                if key: layer[key]=value
                else: layer['font']['family']=value
            return Scene.from_dict(data)
    except Exception:
        for dest in installed:
            try: dest.unlink()
            except OSError: pass
        raise
