#!/usr/bin/env python3.11
"""Generate paper figures via Volcengine ARK seedream-5.0."""
import json, sys, os, urllib.request

ARK_KEY = open(os.path.expanduser("~/.ark_key")).read().strip()
API_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
MODEL = "doubao-seedream-5-0-260128"
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

FIGURES = [
    {
        "name": "fig_teaser",
        "prompt": (
            "A clean, modern infographic-style illustration for an academic paper about AI coding agent skill evolution. "
            "The image shows a conceptual flow: on the left side, multiple developers sitting at computers with colorful code screens, "
            "their coding sessions (represented as glowing trajectory ribbons in blue and purple) flow upward into a central glowing hub labeled with a gear/brain icon. "
            "From the hub, refined skill documents (represented as clean white cards with code snippets) radiate outward back to the developers. "
            "The overall composition suggests a cycle: learn from developer trajectories → distill into skills → distribute back. "
            "Style: flat design, soft pastel colors (blues, purples, greens on white), clean geometric shapes, "
            "minimalist academic illustration style similar to Nature or Science magazine infographics. "
            "NO text, NO labels, NO watermarks. White background. Very clean and professional."
        ),
        "size": "3200x1200",
    },
    {
        "name": "fig_overview_concept",
        "prompt": (
            "A clean schematic illustration for a systems paper showing a three-layer architecture concept. "
            "TOP layer: three human silhouettes (developers) each paired with a laptop showing code. "
            "MIDDLE layer: a horizontal bar representing a 'client daemon' that collects data streams (shown as thin flowing lines from each laptop). "
            "BOTTOM layer: a processing pipeline shown as three connected hexagonal modules in a row, "
            "with the output being a glowing document icon. "
            "Color scheme: soft muted blues and teals, with orange accent for the processing modules. "
            "White background, flat vector style, no text labels, no shadows. "
            "Academic paper illustration quality. Aspect ratio 4:3."
        ),
        "size": "2560x1920",
    },
    {
        "name": "fig_canary_concept",
        "prompt": (
            "A conceptual illustration of A/B testing for software skills. "
            "Two parallel paths shown side by side: "
            "LEFT path (green tinted): a group of three user icons receiving version A of a document, with a satisfaction meter showing 6.8/10. "
            "RIGHT path (orange tinted): a smaller group of two user icons receiving version B (staging), with a satisfaction meter showing 7.4/10. "
            "Below both paths, a balance scale tips toward the right (staging wins). "
            "At the bottom: the winning version gets a green checkmark, the losing version gets a pause/freeze icon. "
            "Clean flat design, soft pastel colors, white background, no text, no labels. "
            "Academic paper quality infographic style."
        ),
        "size": "2048x2048",
    },
    {
        "name": "fig_pipeline_concept",
        "prompt": (
            "A conceptual illustration of a data processing pipeline with three stages. "
            "LEFT: a stack of conversation transcripts (chat bubbles with code snippets) being fed into the pipeline. "
            "STAGE 1 (blue): scissors icon cutting the conversations into small pieces (segments). "
            "STAGE 2 (purple): a sorting/routing mechanism directing pieces into different folders/categories. "
            "STAGE 3 (orange): a writing/synthesis mechanism producing a polished document from the sorted pieces. "
            "RIGHT: a clean, formatted skill document emerges with a git branch icon. "
            "Horizontal left-to-right flow, connected by clean arrows. "
            "Flat vector style, soft pastel colors, white background, NO text, NO labels. "
            "Wide format, academic illustration quality."
        ),
        "size": "3200x1200",
    },
]

def generate(fig):
    name = fig["name"]
    print(f"[{name}] Generating ({fig['size']})...")
    payload = json.dumps({
        "model": MODEL,
        "prompt": fig["prompt"],
        "size": fig["size"],
        "response_format": "url",
        "watermark": False,
        "n": 1
    }).encode()
    req = urllib.request.Request(API_URL, data=payload, headers={
        "Authorization": f"Bearer {ARK_KEY}",
        "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"[{name}] ERROR: {e}")
        return False

    if "error" in data:
        print(f"[{name}] API ERROR: {data['error']}")
        return False

    url = data["data"][0]["url"]
    out_path = os.path.join(OUT_DIR, f"{name}.png")
    urllib.request.urlretrieve(url, out_path)
    size = os.path.getsize(out_path)
    print(f"[{name}] OK -> {out_path} ({size:,} bytes)")
    return True

if __name__ == "__main__":
    ok = 0
    for fig in FIGURES:
        if generate(fig):
            ok += 1
    print(f"\n=== {ok}/{len(FIGURES)} figures generated ===")
