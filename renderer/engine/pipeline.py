"""Public v2 rendering facade; the scene engine has no GUI dependency."""
from dataclasses import dataclass
from scene import Scene
import renderer

@dataclass
class RenderRequest:
    scene: Scene
    text: str | None = None

def render(request):
    return renderer.render(request.scene,request.text)

def render_to_bytes(request,format='png'):
    return renderer.render_to_bytes(request.scene,request.text,format)

def render_to_file(request,path):
    return renderer.render_to_file(request.scene,path,request.text)
