#!/usr/bin/env python3.11
"""Generate all 4 paper figures via apicore.ai image generation API."""
import json, sys, os, base64, re, time, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

API_KEY = open(os.path.expanduser("~/.apicore_key")).read().strip()
API_URL = "https://api.apicore.ai/v1/chat/completions"
MODEL = "gpt-4o-image"
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

FIGURES = {
    "fig1_overview_ai": """Create a clean, professional academic paper figure showing a system architecture diagram for "xskill".

The diagram should show a TOP-TO-BOTTOM flow with these layers:
- TOP ROW: Three developers (Developer A, B, C) each using a different coding agent (Claude Code, Codex, OpenCode) shown as rounded rectangle cards
- MIDDLE: A wide green-tinted box labeled "xskill client" with subtitle "trajectory watcher + skill sync"
- A bidirectional arrow between client and server labeled "traj upload / skill download"
- A wide orange-tinted box labeled "xskill server" containing three connected modules in a horizontal pipeline: TaskAgent → TaskClusterAgent → SkillEditAgent
- BOTTOM: A box labeled "Skill Repo (git versioned)" receiving output from the pipeline

Style requirements:
- Use soft, muted pastel colors (light blue, light green, light orange) - NOT bright/saturated
- Rounded rectangles with thin borders for all boxes
- Clean sans-serif font
- Arrows should be simple and clean with small arrowheads
- VERY COMPACT layout with minimal whitespace between elements
- White background
- NO decorative elements, NO shadows, NO gradients
- This is for an academic paper - it must look professional and clean like a systems paper figure
- Aspect ratio approximately 3:4 (portrait-ish)""",

    "fig3_pipeline_ai": """Create a clean, professional academic paper figure showing a data processing pipeline called "Three-Agent Pipeline".

The diagram should show a LEFT-TO-RIGHT horizontal flow:
1. Input box (light gray): "Trajectory"
2. First processing module (blue-tinted card): "TaskAgent" with small label "decompose" above it, and an output "atoms" shown as a small box below
3. Arrow labeled "per atom" connecting to second module
4. Second processing module (blue-tinted card): "TaskClusterAgent" with label "cluster" above, and output "candidates" below
5. Arrow labeled "Σ weight ≥ θ" connecting to third module
6. Third processing module (blue-tinted card): "SkillEditAgent" with label "produce" above
7. Output box (light gray): "SKILL.md + git commit"
8. Below the pipeline, a yellow-tinted annotation box: "cumulative evidence threshold (θ=10)"

Style requirements:
- Soft muted pastel blue cards for the three agents (these are the key contribution - make them slightly more prominent with thicker borders)
- Light gray for input/output boxes
- Clean sans-serif font throughout
- Simple thin arrows with small arrowheads
- VERY COMPACT - minimal spacing between elements, no wasted whitespace
- White background, NO shadows, NO gradients, NO decorative elements
- Wide aspect ratio approximately 4:1 (landscape)
- Academic paper quality - professional and understated""",

    "fig4_canary_ai": """Create a clean, professional academic paper figure showing an A/B testing protocol called "Canary Evaluation Protocol".

The diagram should show a TOP-TO-BOTTOM flow:
- TOP LEFT: Green-tinted rounded box labeled "main branch" with three small circle nodes below it labeled u₁, u₂, u₃, and a dashed box below showing "s̄_main = 6.8"
- TOP RIGHT: Orange-tinted rounded box labeled "staging branch" with two small circle nodes below labeled u₄, u₅, and a dashed box showing "s̄_staging = 7.4"
- MIDDLE: A diamond decision shape asking "s̄_staging > s̄_main?" with annotation "(Welch's t-test, α=0.05, n_min=20)"
- Arrows from both score boxes converging into the diamond
- BOTTOM LEFT: Green box "PROMOTE" with subtitle "staging → new main" (connected by "yes" arrow from diamond)
- BOTTOM RIGHT: Red-tinted box "FREEZE" with subtitle "retain for audit" (connected by "no" arrow from diamond)

Style requirements:
- Soft pastel green for main branch elements, soft pastel orange for staging elements
- Soft pastel red for FREEZE outcome
- Clean sans-serif font
- Simple arrows with small arrowheads
- VERY COMPACT layout - elements close together, minimal whitespace
- White background, NO shadows, NO gradients
- Portrait-ish aspect ratio approximately 3:4
- Academic paper quality""",

    "fig2_collection_ai": """Create a clean, professional academic paper figure showing a data collection architecture.

The diagram should show a LEFT-TO-RIGHT flow:
- LEFT COLUMN: Five stacked source boxes (light blue tinted, compact spacing):
  1. "Claude Code" with small badge "JSONL"
  2. "Codex" with small badge "JSONL"
  3. "OpenClaw" with small badge "JSONL"
  4. "OpenCode" with small badge "SQLite"
  5. "Hermes" with small badge "JSONL"
- CENTER: A larger green-tinted box labeled "Trajectory Normalizer" with schema fields listed inside: "source, session_id, ts, kind, data"
- RIGHT: An output box (orange-tinted) labeled "Unified Trajectory"
- Arrows from each of the 5 sources pointing to the normalizer, then one arrow from normalizer to output

Style requirements:
- Soft muted pastel colors - light blue for sources, light green for normalizer, light orange for output
- Clean sans-serif font
- Compact stacking of the 5 source boxes (very little gap between them)
- Simple thin arrows
- VERY COMPACT overall - no wasted whitespace
- White background, NO shadows, NO gradients, NO decorative elements
- Wide aspect ratio approximately 3:1 (landscape)
- Academic paper quality - must look like a figure from a top-tier systems paper"""
}

def generate_one(name, prompt):
    """Generate one figure, save to PNG."""
    import urllib.request
    print(f"[{name}] Generating...")
    payload = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096
    }).encode()
    req = urllib.request.Request(API_URL, data=payload, headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"[{name}] ERROR: {e}")
        return name, False

    content = data["choices"][0]["message"]["content"]

    # Try to extract image URL
    url_match = re.search(r'https?://[^\s\)\"]+\.(?:png|jpg|jpeg|webp)', content)
    if url_match:
        img_url = url_match.group(0)
        out_path = os.path.join(OUT_DIR, f"{name}.png")
        subprocess.run(["curl", "-sL", "-o", out_path, img_url], timeout=60)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
            print(f"[{name}] OK (url) -> {out_path} ({os.path.getsize(out_path)} bytes)")
            return name, True

    # Try base64
    b64_match = re.search(r'data:image/[^;]+;base64,([A-Za-z0-9+/=]+)', content)
    if b64_match:
        out_path = os.path.join(OUT_DIR, f"{name}.png")
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(b64_match.group(1)))
        print(f"[{name}] OK (b64) -> {out_path} ({os.path.getsize(out_path)} bytes)")
        return name, True

    # Try markdown image
    md_match = re.search(r'!\[.*?\]\((https?://[^\)]+)\)', content)
    if md_match:
        img_url = md_match.group(1)
        out_path = os.path.join(OUT_DIR, f"{name}.png")
        subprocess.run(["curl", "-sL", "-o", out_path, img_url], timeout=60)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
            print(f"[{name}] OK (md) -> {out_path} ({os.path.getsize(out_path)} bytes)")
            return name, True

    print(f"[{name}] FAILED - no image found in response")
    print(f"[{name}] Content preview: {content[:300]}")
    return name, False

if __name__ == "__main__":
    results = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(generate_one, n, p): n for n, p in FIGURES.items()}
        for f in as_completed(futures):
            name, ok = f.result()
            results[name] = ok

    print("\n=== Results ===")
    for n, ok in results.items():
        status = "OK" if ok else "FAILED"
        print(f"  {n}: {status}")
