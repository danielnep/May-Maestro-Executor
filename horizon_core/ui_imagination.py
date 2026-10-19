"""Horizon UI imagination: deterministic mental-model analogue, before rendering.

Predicts constraints and test scenarios from project files without LLM calls.
Predictions are not observations. Chromium remains the authority for actual layout.
"""
import hashlib
from pathlib import Path
import re

UI_AGENTS=frozenset(("frontend-engineer","ui-ux-designer",
                     "horizon-ui-guardian","mobile-app-builder"))
VIEWPORTS=(
    {"name":"mobile","width":390,"height":844},
    {"name":"desktop","width":1440,"height":960},
)
MAX_STYLES=12
MAX_STYLE_BYTES=1_500_000


def imagine_interface(root, objective, *, agent):
    if agent not in UI_AGENTS:
        return None
    root=Path(root).resolve()
    if not isinstance(objective,str):
        raise ValueError("UI imagination requires an objective")
    css=[]
    media=[]
    contents=0
    for path in sorted(root.rglob("*.css")):
        if len(css)>=MAX_STYLES or contents>=MAX_STYLE_BYTES:
            break
        if any(part.startswith(".") or part in ("node_modules","runtime","credentials","secrets")
               for part in path.relative_to(root).parts) or path.is_symlink():
            continue
        size=path.stat().st_size
        if size>MAX_STYLE_BYTES-contents:
            continue
        source=path.read_text(encoding="utf-8",errors="replace")
        contents+=size
        hits=re.findall(r"@media\s*\((?:max|min)-width\s*:\s*(\d+)px\)",source,re.I)
        media.extend(int(x) for x in hits)
        css.append({"path":path.relative_to(root).as_posix(),"bytes":size,"breakpoints":sorted(set(int(x) for x in hits))[:16]})
    requested=objective.lower()
    focus=[]
    terms=(("mensag","message-panel"),("chat","message-panel"),
           ("notifica","notifications"),("barra","navigation-bars"),
           ("menu","navigation"),("bot","tap-targets"),("cliq","tap-targets"),
           ("mapa","map-viewport"),("formul","form-keyboard"),
           ("modal","dialog"),("seta","icon-alignment"))
    for term,feature in terms:
        if term in requested and feature not in focus:focus.append(feature)
    if not focus:focus=["content-hierarchy","navigation","tap-targets"]
    scenes=[]
    for screen in VIEWPORTS:
        w,h=screen["width"],screen["height"]
        scenes.append({
            "name":screen["name"],"width":w,"height":h,
            "expected_content_width_px":w,
            "minimum_tap_target_px":44 if screen["name"]=="mobile" else 24,
            "allow_horizontal_overflow":False,
            "expected_behavior":[
                "navigation and visible controls must be reachable",
                "panels and dialogs must remain within available viewport",
                "interactive controls must receive real click/focus events",
            ],
            "focus":focus,
            "candidate_media_breakpoints":sorted(set(media))[:24],
            "prediction_only":True})
    return {
        "kind":"deterministic_ui_imagination",
        "evidence":"project_css_and_user_objective_not_rendered_pixels",
        "agent":agent,"viewports":scenes,"style_sources":css,
        "objective_sha256":hashlib.sha256(objective.encode("utf-8")).hexdigest(),
        "requires_real_browser_validation":True,
        "vision_api_calls":0,
    }
