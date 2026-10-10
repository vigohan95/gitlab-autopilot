#!/usr/bin/env python3
"""Produce the Devpost demo video for Autopilot from the real, public GitLab pages — no camera, no mic, no paid tools.

Pipeline (all free / open source):
  1. narrate   edge-tts renders one MP3 per shot from the voice-over in SHOTS (Microsoft neural voice, no key).
  2. record    Playwright (Chromium 1920x1080, headless) opens the public GitLab pages (no sign-in), scrolls to the
               evidence and captures frames through the CDP screencast with wall-clock timestamps; a caption strip
               and a fake cursor are injected so the viewer can follow.
  3. assemble  ffmpeg resamples the frames to 30 fps H.264 segments (one per shot), adds a title card and an end
               card, muxes the narration as AAC, writes the SRT and a 1280x720 thumbnail.
  4. verify    ffprobe: duration <= 178 s, audio + video streams, sampled frames are not black, audio has signal.

Requirements: Python 3.11+, `pip install playwright edge-tts` + `python -m playwright install chromium`,
ffmpeg/ffprobe on PATH (or in tools/ffmpeg/, or FFMPEG=/path). On Windows run with PYTHONUTF8=1.

Usage:
  python scripts/make_video.py                         # everything -> video/autopilot-demo.mp4
  python scripts/make_video.py --step assemble         # re-cut from frames/narration already on disk
  python scripts/make_video.py --duo-session <URL>     # adds the AI > Sessions shot (hackathon project) after shot 2

Outputs (video/, git-ignored): autopilot-demo.mp4, autopilot-demo.srt, autopilot-demo-thumb.png, shots.json, stills/.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HIST = "https://gitlab.com/hanwg666/gitlab-autopilot"
SHOW = "https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase"
PROD = "https://pulse-prod-z0fn.onrender.com"
FPS = 30
HARD_CAP = 178.0  # Devpost: under 3:00, keep margin
CARD_SECONDS = 3.0
NARRATION_LEAD = 0.35
SHOT_TAIL = 0.7
ZOOM = "1.15"

# ------------------------------------------------------------------------------------------------------------
# Shot list: (caption, voice-over). Honest about what ran: a demo script opened the issue, CI jobs did the rest.
# ------------------------------------------------------------------------------------------------------------
SHOTS: list[tuple[str, str]] = [
    (
        "One issue in, production out",
        "Code generation is solved. Autopilot is about everything after the code: one issue goes in, production comes out. Nine DevSecOps stages on GitLab CI, designed around the GitLab Duo Agent Platform, with a written policy that every step cites.",
    ),
    (
        "Issue #1 — the only input",
        "It starts with one issue and one label: autopilot. In this recording the issue was opened by our demo script. From here on, nothing is clicked by a person.",
    ),
    (
        "MR !1 — policy as code",
        "A merge request appears. Policy as code: AUTOPILOT dot M D says what may be merged without a human. The policy check cites the rules it applied: scope, protected paths, size, no conflicts. It labels the request approved, and the auto-merge job merges when the pipeline is green. Every note ends with the rule numbers.",
    ),
    (
        "MR !3 — the security gate holds",
        "Security is a loop, not a report. Here a dependency bump pulls a vulnerable web framework. The scanners run from the first second, next to the tests. The tests are green, and pip-audit is red: five advisories. The gate holds. Nothing merges.",
    ),
    (
        "MR !3 — fixed and merged",
        "The fix is one pin, labelled security-fix, citing section four of the policy. Scanner green, policy green, merged. Same gate, same rules.",
    ),
    (
        "Releases, image, wheel",
        "On main, every merge becomes a package: an image in the container registry, a wheel in the package registry, and when the version changes, release-cli cuts a tagged release with generated notes. Three releases, all cut by the pipeline.",
    ),
    (
        "Staging → production on Render",
        "Deployment promotes from staging to production on Render's free tier, by exact commit. This is production, live.",
    ),
    (
        "Incident #3 — self-healing",
        "A scheduled monitor watches it. We broke production on purpose: the monitor went red, the pipeline opened this incident, cleared the fault on the live service, and rolled back to the previous release. Never forward. The rollback pipeline and its note are on the incident.",
    ),
    (
        "Journey card — honest by design",
        "Back on the original issue: the journey card. Nine stages, which jobs covered them, how long, how many retries. The first card over-claimed; it is marked superseded, and the corrected one names the real actors. Honesty is part of the design.",
    ),
    (
        "Policy, flows, pipeline — one repo",
        "The policy file, the flow and agent definitions for the Duo Agent Platform, and the pipeline live side by side in the repository. Nine stages. One issue. Autopilot.",
    ),
]
DUO_SHOT = (
    "GitLab Duo Agent Platform — a real run",
    "In the hackathon project the work is started by a GitLab Duo trigger. The Planner-and-Developer flow posts its plan on the issue, citing the policy, then writes the code and the tests and opens the merge request. GitLab Duo Code Review reads it; the policy check approves. The agents did the work; the pipeline kept the gate.",
)

# ------------------------------------------------------------------------------------------------------------
# tools
# ------------------------------------------------------------------------------------------------------------


def find_tool(name: str) -> str:
    env = os.environ.get(name.upper())
    if env and Path(env).exists():
        return env
    on_path = shutil.which(name)
    if on_path:
        return on_path
    for root in (REPO / "tools" / "ffmpeg", REPO.parents[2] / "tools" / "ffmpeg"):
        if root.exists():
            for p in root.rglob(name + (".exe" if os.name == "nt" else "")):
                return str(p)
    sys.exit(f"{name} not found (PATH, tools/ffmpeg/, or {name.upper()}=path)")


FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)  # noqa: S603
    if r.returncode != 0:
        print(r.stdout[-3000:], r.stderr[-3000:], file=sys.stderr)
        raise SystemExit(f"command failed ({r.returncode}): {' '.join(cmd[:4])} ...")
    return r


def media_duration(path: Path) -> float:
    return float(
        run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]).stdout.strip()
    )


# ------------------------------------------------------------------------------------------------------------
# 1. narration
# ------------------------------------------------------------------------------------------------------------


def narrate(work: Path, shots: list[tuple[str, str]], voice: str, rate: str) -> list[float]:
    import edge_tts  # type: ignore

    vo_dir = work / "vo"
    vo_dir.mkdir(parents=True, exist_ok=True)

    async def render() -> None:
        for i, (_, text) in enumerate(shots, 1):
            out, stamp = vo_dir / f"vo{i}.mp3", vo_dir / f"vo{i}.txt"
            key = f"{voice}|{rate}|{text}"
            if out.exists() and out.stat().st_size > 0 and stamp.exists() and stamp.read_text(encoding="utf-8") == key:
                continue
            await edge_tts.Communicate(text, voice, rate=rate).save(str(out))
            stamp.write_text(key, encoding="utf-8")

    asyncio.run(render())
    durations = [media_duration(vo_dir / f"vo{i}.mp3") for i in range(1, len(shots) + 1)]
    for i, d in enumerate(durations, 1):
        print(f"  narration {i}: {d:5.2f}s  {shots[i - 1][0]}")
    print(f"  narration total: {sum(durations):.1f}s")
    return durations


# ------------------------------------------------------------------------------------------------------------
# 2. recording
# ------------------------------------------------------------------------------------------------------------

OVERLAY_JS = r"""
(caption) => {
  // GitLab's CSP blocks injected <style> elements; style through the CSSOM instead (not subject to style-src).
  const css = (el, props) => Object.assign(el.style, props);
  for (const sel of ['.js-cookie-consent', '#onetrust-consent-sdk', '.gl-broadcast-message', '[data-testid="broadcast-notification-container"]']) {
    document.querySelectorAll(sel).forEach(el => { el.style.display = 'none'; });
  }
  let cap = document.getElementById('demo-cap');
  if (!cap) {
    cap = document.createElement('div'); cap.id = 'demo-cap';
    css(cap, { position: 'fixed', left: '0', right: '0', bottom: '0', height: '46px', background: '#0f172a', color: '#fff',
      font: '500 16px/1 ui-sans-serif, system-ui, "Segoe UI", sans-serif', display: 'flex', alignItems: 'center',
      padding: '0 22px', gap: '14px', zIndex: '2147483646', letterSpacing: '.01em', zoom: '1' });
    const m = document.createElement('span'); m.textContent = 'Autopilot demo'; css(m, { opacity: '.55' });
    const b = document.createElement('span'); b.id = 'demo-cap-text'; css(b, { fontWeight: '700' });
    const r = document.createElement('span'); r.textContent = 'gitlab.com/hanwg666/gitlab-autopilot  \u00b7  real pipeline history, no sign-in';
    css(r, { marginLeft: 'auto', opacity: '.7', fontSize: '13px' });
    cap.append(m, b, r); document.body.appendChild(cap);
    const cur = document.createElement('div'); cur.id = 'demo-cur';
    const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="26" height="26"><path d="M3 2 L3 20 L8 15.5 L11.5 22.5 L14.5 21 L11 14.5 L17.5 14.5 Z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    css(cur, { position: 'fixed', left: '0', top: '0', width: '26px', height: '26px', zIndex: '2147483647', pointerEvents: 'none',
      transform: 'translate(-100px,-100px)', transition: 'transform 16ms linear', background: 'url("data:image/svg+xml;utf8,' + encodeURIComponent(svg) + '") no-repeat',
      filter: 'drop-shadow(0 1px 1.5px rgba(0,0,0,.5))' });
    document.body.appendChild(cur);
    document.addEventListener('mousemove', e => { cur.style.transform = `translate(${e.clientX - 2}px, ${e.clientY - 1}px)`; }, true);
  }
  document.getElementById('demo-cap-text').textContent = caption;
}
"""


@dataclass
class Recorder:
    page: object
    cdp: object
    frames_dir: Path
    shots_def: list[tuple[str, str]]
    frames: list[tuple[float, str]] = field(default_factory=list)
    shots: list[dict] = field(default_factory=list)
    pos: tuple[float, float] = (960.0, 540.0)
    _n: int = 0
    _caption: str = ""

    def start(self) -> None:
        self.cdp.on("Page.screencastFrame", self._on_frame)
        self.cdp.send(
            "Page.startScreencast",
            {"format": "jpeg", "quality": 92, "maxWidth": 1920, "maxHeight": 1080, "everyNthFrame": 1},
        )

    def _on_frame(self, ev: dict) -> None:
        self._n += 1
        path = self.frames_dir / f"f{self._n:06d}.jpg"
        path.write_bytes(base64.b64decode(ev["data"]))
        self.frames.append((float(ev["metadata"]["timestamp"]), path.name))
        self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})

    def stop(self) -> None:
        try:
            self.cdp.send("Page.stopScreencast")
        except Exception:  # noqa: BLE001
            pass

    def pause(self, seconds: float) -> None:
        self.page.wait_for_timeout(int(seconds * 1000))

    def caption(self, text: str | None = None) -> None:
        if text is not None:
            self._caption = text
        try:
            self.page.evaluate(OVERLAY_JS, self._caption)
        except Exception:  # noqa: BLE001 - page mid-navigation
            pass

    def goto(self, url: str, settle: float = 2.5, zoom: str | None = ZOOM) -> None:
        """Navigate, hide banners, apply zoom, re-inject the overlay (navigation drops it)."""
        try:
            self.page.goto(url, wait_until="load", timeout=90_000)
        except Exception as exc:  # noqa: BLE001
            print(f"  goto {url}: {type(exc).__name__} (continuing)")
        self.pause(settle)
        if zoom:
            try:
                self.page.evaluate("(z) => { document.body.style.zoom = z; }", zoom)
            except Exception:  # noqa: BLE001
                pass
        self.caption()
        self.page.mouse.move(*self.pos)

    def begin(self, index: int) -> None:
        self.caption(f"{index} / {len(self.shots_def)}  —  {self.shots_def[index - 1][0]}")
        self.pause(0.25)
        self.shots.append({"index": index, "title": self.shots_def[index - 1][0], "start": time.time()})

    def end(self, min_len: float) -> None:
        shot = self.shots[-1]
        remaining = min_len - (time.time() - shot["start"])
        if remaining > 0:
            self.pause(remaining)
        shot["end"] = time.time()
        shot["seconds"] = round(shot["end"] - shot["start"], 2)
        print(f"  shot {shot['index']}: {shot['seconds']:.1f}s  {shot['title']}")

    def move(self, x: float, y: float, dur: float | None = None) -> None:
        x0, y0 = self.pos
        dist = math.hypot(x - x0, y - y0)
        if dist < 1:
            return
        dur = dur if dur is not None else min(1.0, 0.3 + dist / 1800)
        t0 = time.time()
        while True:
            t = min(1.0, (time.time() - t0) / dur)
            e = t * t * (3 - 2 * t)
            self.page.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
            if t >= 1.0:
                break
            self.pause(0.012)
        self.pos = (x, y)

    def scroll_to_text(self, text: str, dy: int = -140, settle: float = 1.2) -> bool:
        """Smooth-scroll the first element containing `text` into view (returns False if not found)."""
        try:
            loc = self.page.get_by_text(text, exact=False).first
            loc.wait_for(state="attached", timeout=6000)
            # scrollIntoView works inside GitLab's nested scroll containers; window.scrollBy does not
            self.page.evaluate(
                "(el) => el.scrollIntoView({ behavior: 'smooth', block: 'center' })", loc.element_handle()
            )
            self.pause(settle)
            box = loc.bounding_box()
            if box:
                self.move(min(1800, box["x"] + 40), min(1000, box["y"] + box["height"] / 2), 0.6)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"  (text not found: {text[:40]!r}: {type(exc).__name__})")
            return False

    def reveal(self, text: str, dy: int = -140, settle: float = 1.2, passes: int = 3) -> bool:
        """GitLab renders discussion notes lazily: scroll every scrollable area to the bottom a few times, then find the text."""
        for _ in range(passes):
            try:
                self.page.evaluate(
                    "() => { const se = document.scrollingElement; se.scrollTo({ top: se.scrollHeight, behavior: 'smooth' });"
                    " document.querySelectorAll('main, .content-wrapper, [data-testid=\"work-item-notes\"]').forEach(el => { try { el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' }); } catch (e) {} });"
                    " const last = document.querySelector('#notes-list li:last-child, .notes li:last-child, [data-testid=\"work-item-notes\"] li:last-child'); if (last) last.scrollIntoView({ block: 'end' }); }"
                )
            except Exception:  # noqa: BLE001
                pass
            self.pause(1.1)
        return self.scroll_to_text(text, dy=dy, settle=settle)

    def find_text(self, text: str, settle: float = 1.4) -> bool:
        """Plain-text pages (raw job log): let Chromium's find scroll the match into view."""
        try:
            ok = bool(self.page.evaluate("(q) => window.find(q, false, false, true, false, true, false)", text))
        except Exception:  # noqa: BLE001
            ok = False
        self.pause(settle)
        return ok

    def scroll_by(self, px: int, settle: float = 1.0) -> None:
        self.page.evaluate("(px) => window.scrollBy({ top: px, behavior: 'smooth' })", px)
        self.pause(settle)

    def still(self, out_dir: Path, name: str) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.page.screenshot(path=str(out_dir / f"{name}.png"))
        except Exception:  # noqa: BLE001
            pass


def record(
    work: Path,
    out: Path,
    shots_def: list[tuple[str, str]],
    narration: list[float],
    duo: tuple[str, str | None] | None,
    headed: bool,
) -> list[dict]:
    from playwright.sync_api import sync_playwright  # type: ignore

    frames_dir = work / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)
    stills = out / "stills"
    need = [d + NARRATION_LEAD + SHOT_TAIL for d in narration]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=1,
            color_scheme="light",
            locale="en-US",
            timezone_id="UTC",
        )
        page = ctx.new_page()
        rec = Recorder(page=page, cdp=ctx.new_cdp_session(page), frames_dir=frames_dir, shots_def=shots_def)
        rec.goto(HIST, settle=3.0)
        rec.start()
        rec.pause(0.8)
        if not rec.frames:
            raise SystemExit("screencast produced no frames")
        n = 0

        def shot(idx_title: str):
            nonlocal n
            n += 1
            assert shots_def[n - 1][0] == idx_title, (n, idx_title, shots_def[n - 1][0])
            rec.begin(n)
            return n

        # 1 README + architecture
        shot("One issue in, production out")
        rec.still(stills, "01-readme")
        rec.pause(1.2)
        rec.scroll_to_text("Architecture", dy=-80, settle=1.5)
        rec.scroll_by(420, 1.6)
        rec.scroll_by(420, 1.6)
        rec.end(need[n - 1])

        # 2 issue #1
        shot("Issue #1 — the only input")
        rec.goto(f"{HIST}/-/issues/1")
        rec.still(stills, "02-issue-1")
        rec.scroll_to_text("autopilot", dy=-200, settle=1.0)
        rec.pause(1.0)
        rec.end(need[n - 1])

        # optional Duo shot: the flow's plan note on the hackathon issue, then the MR it opened (public pages)
        if duo:
            shot(DUO_SHOT[0])
            rec.goto(duo[0], settle=3.5)
            rec.reveal("has started", dy=-120, settle=1.2, passes=2)
            rec.reveal("Plan for", dy=-100, settle=1.4, passes=1)
            rec.still(stills, "02b-duo-plan-note")
            rec.scroll_by(420, 2.2)
            if duo[1]:
                rec.goto(duo[1], settle=3.5)
                rec.reveal("I finished my review", dy=-120, settle=1.4, passes=2)
                rec.still(stills, "02c-duo-mr-review")
                rec.scroll_by(380, 2.0)
            rec.end(need[n - 1])

        # 3 MR !1 notes + pipeline
        shot("MR !1 — policy as code")
        rec.goto(f"{HIST}/-/merge_requests/1")
        rec.still(stills, "03-mr1")
        if rec.reveal("Autopilot policy check", dy=-160, settle=1.4):
            rec.still(stills, "03a-policy-note")
            rec.pause(2.0)
        rec.scroll_to_text("Merging when this pipeline succeeds", dy=-200, settle=1.4)
        rec.pause(1.5)
        rec.goto(f"{HIST}/-/pipelines/2930687996", settle=3.5)
        rec.still(stills, "03b-mr1-pipeline")
        rec.end(need[n - 1])

        # 4 MR !3 red pipeline + pip-audit log
        shot("MR !3 — the security gate holds")
        rec.goto(f"{HIST}/-/pipelines/2930920076", settle=3.5)
        rec.still(stills, "04-mr3-red-pipeline")
        rec.pause(2.5)
        rec.goto(f"{HIST}/-/jobs/17062977133/raw", settle=2.5, zoom="1.35")
        try:
            page.evaluate("() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' })")
        except Exception:  # noqa: BLE001
            pass
        rec.pause(1.0)
        rec.find_text("Found 10 known vulnerabilities", settle=1.6)
        rec.find_text("PYSEC-2026-248", settle=1.6)
        rec.still(stills, "04b-pip-audit-log")
        rec.end(need[n - 1])

        # 5 MR !3 merged + green pipeline
        shot("MR !3 — fixed and merged")
        rec.goto(f"{HIST}/-/merge_requests/3")
        rec.still(stills, "05-mr3-merged")
        rec.scroll_to_text("Security fix", dy=-160, settle=1.4)
        rec.pause(1.0)
        rec.goto(f"{HIST}/-/pipelines/2930937314", settle=3.0)
        rec.end(need[n - 1])

        # 6 releases, registry, packages
        shot("Releases, image, wheel")
        rec.goto(f"{HIST}/-/releases", settle=3.0)
        rec.still(stills, "06-releases")
        rec.scroll_by(300, 1.5)
        rec.goto(f"{HIST}/container_registry", settle=3.0)
        rec.pause(1.0)
        rec.goto(f"{HIST}/-/packages", settle=3.0)
        rec.end(need[n - 1])

        # 7 environments + production
        shot("Staging → production on Render")
        rec.goto(f"{HIST}/-/environments", settle=3.0)
        rec.still(stills, "07-environments")
        rec.pause(1.5)
        rec.goto(f"{PROD}/", settle=3.0, zoom="1.9")
        rec.still(stills, "07b-production")
        rec.end(need[n - 1])

        # 8 incident #3 + rollback pipeline
        shot("Incident #3 — self-healing")
        rec.goto(f"{HIST}/-/issues/3")
        rec.still(stills, "08-incident-3")
        rec.scroll_to_text("Automatic remediation", dy=-160, settle=1.4)
        rec.reveal("Rollback to", dy=-200, settle=1.4, passes=2)
        rec.still(stills, "08a-incident-notes")
        rec.pause(1.0)
        rec.goto(f"{HIST}/-/pipelines/2933092058", settle=3.5)
        rec.still(stills, "08b-rollback-pipeline")
        rec.end(need[n - 1])

        # 9 corrected journey card
        shot("Journey card — honest by design")
        rec.goto(f"{HIST}/-/issues/1")
        rec.reveal("SUPERSEDED", dy=-120, settle=1.4)
        rec.pause(1.0)
        rec.reveal("Corrected journey card", dy=-120, settle=1.4, passes=1)
        rec.scroll_by(380, 1.8)
        rec.still(stills, "09-journey-card")
        rec.end(need[n - 1])

        # 10 AUTOPILOT.md + .gitlab/duo
        shot("Policy, flows, pipeline — one repo")
        rec.goto(f"{HIST}/-/blob/main/AUTOPILOT.md")
        rec.scroll_to_text("autopilot_policy: 1", dy=-200, settle=1.6)
        rec.still(stills, "10-autopilot-md")
        rec.goto(f"{HIST}/-/tree/main/.gitlab/duo/flows", settle=3.0)
        rec.end(need[n - 1])

        rec.stop()
        shots, frames = rec.shots, rec.frames
        browser.close()

    if len(shots) != len(shots_def):
        raise SystemExit(f"recorded {len(shots)} shots but the shot list has {len(shots_def)}")
    (work / "frames.json").write_text(json.dumps(frames), encoding="utf-8")
    (work / "shots.json").write_text(json.dumps(shots, indent=1), encoding="utf-8")
    print(f"  captured {len(frames)} frames")
    return shots


# ------------------------------------------------------------------------------------------------------------
# cards
# ------------------------------------------------------------------------------------------------------------

BASE_CSS = """
  * { box-sizing: border-box; margin: 0; }
  html, body { width: 1920px; height: 1080px; overflow: hidden; background: #0b1220; color: #fff;
    font-family: ui-sans-serif, system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  .bg { position: absolute; inset: 0; background:
    radial-gradient(900px 600px at 15% 20%, rgba(252,109,38,.30), transparent 60%),
    radial-gradient(700px 500px at 85% 85%, rgba(226,67,41,.18), transparent 60%), #0b1220; }
  .mark { display: inline-grid; place-items: center; width: 72px; height: 72px; border-radius: 18px; background: #fc6d26;
    font-weight: 800; font-size: 40px; }
"""


def title_card_html() -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{BASE_CSS}
  .wrap {{ position: absolute; inset: 0; display: grid; place-items: center; text-align: center; }}
  h1 {{ font-size: 120px; letter-spacing: -.02em; margin-top: 26px; }}
  p.tag {{ font-size: 40px; margin-top: 18px; color: rgba(255,255,255,.85); }}
  p.ev {{ font-size: 26px; margin-top: 56px; letter-spacing: .12em; text-transform: uppercase; color: rgba(255,255,255,.6); }}
  </style></head><body><div class="bg"></div><div class="wrap"><div>
  <div class="mark">A</div><h1>Autopilot</h1>
  <p class="tag">One issue in, production out — nine DevSecOps stages, policy as code.</p>
  <p class="ev">GitLab · Life After Code hackathon · Path A · Hands-off</p></div></div></body></html>"""


def end_card_html() -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{BASE_CSS}
  .wrap {{ position: absolute; inset: 0; display: grid; place-items: center; text-align: center; }}
  h1 {{ font-size: 72px; letter-spacing: -.02em; margin-top: 22px; }}
  .url {{ font-size: 36px; margin-top: 34px; font-family: ui-monospace, Consolas, monospace; color: #fdba74; }}
  .url small {{ display: block; font-size: 20px; color: rgba(255,255,255,.55); margin-bottom: 8px; font-family: ui-sans-serif, system-ui, sans-serif; letter-spacing: .1em; text-transform: uppercase; }}
  p.ft {{ font-size: 24px; margin-top: 56px; color: rgba(255,255,255,.6); }}
  </style></head><body><div class="bg"></div><div class="wrap"><div>
  <div class="mark">A</div><h1>Autopilot</h1>
  <div class="url"><small>Hackathon project (Duo Agent Platform)</small>{SHOW.replace("https://", "")}</div>
  <div class="url"><small>History: pipelines, releases, incidents</small>{HIST.replace("https://", "")}</div>
  <p class="ft">GitLab CI · AUTOPILOT.md policy engine · release-cli · Render · GitLab Duo Agent Platform flows &amp; agents · MIT</p></div></div></body></html>"""


def thumbnail_html(bg_png: Path) -> str:
    data = base64.b64encode(bg_png.read_bytes()).decode()
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
  * {{ box-sizing: border-box; margin: 0 }} html, body {{ width: 1280px; height: 720px; overflow: hidden; font-family: ui-sans-serif, system-ui, "Segoe UI", sans-serif; color: #fff; }}
  .shot {{ position: absolute; inset: 0; background: url(data:image/png;base64,{data}) center/cover; }}
  .shade {{ position: absolute; inset: 0; background: linear-gradient(90deg, rgba(11,18,32,.96) 0%, rgba(11,18,32,.85) 42%, rgba(11,18,32,.15) 70%, rgba(11,18,32,0) 100%); }}
  .txt {{ position: absolute; left: 64px; top: 150px; width: 600px; }}
  .mark {{ display: inline-grid; place-items: center; width: 64px; height: 64px; border-radius: 16px; background: #fc6d26; font-weight: 800; font-size: 36px; }}
  h1 {{ font-size: 92px; letter-spacing: -.03em; margin-top: 16px; line-height: 1; }}
  p {{ font-size: 30px; margin-top: 18px; line-height: 1.25; color: rgba(255,255,255,.9); }}
  .pill {{ display: inline-block; margin-top: 28px; padding: 10px 16px; border-radius: 999px; background: rgba(255,255,255,.12); font-size: 20px; letter-spacing: .06em; text-transform: uppercase; }}
  </style></head><body><div class="shot"></div><div class="shade"></div><div class="txt"><div class="mark">A</div><h1>Autopilot</h1>
  <p>One issue in, production out. Nine DevSecOps stages, policy as code, on GitLab.</p><span class="pill">GitLab Life After Code · demo</span></div></body></html>"""


def render_html_png(html: str, out: Path, width: int, height: int) -> None:
    from playwright.sync_api import sync_playwright  # type: ignore

    src = out.with_suffix(".html")
    src.write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
        pg.goto(src.as_uri(), wait_until="load")
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(out))
        b.close()


# ------------------------------------------------------------------------------------------------------------
# 3. assembly
# ------------------------------------------------------------------------------------------------------------

X264 = [
    "-c:v",
    "libx264",
    "-preset",
    "medium",
    "-crf",
    "19",
    "-pix_fmt",
    "yuv420p",
    "-r",
    str(FPS),
    "-g",
    str(FPS * 2),
    "-profile:v",
    "high",
    "-level",
    "4.1",
]


def frames_for_shot(frames: list[tuple[float, str]], start: float, end: float) -> list[tuple[str, float]]:
    idx = 0
    for i, (ts, _) in enumerate(frames):
        if ts <= start:
            idx = i
        else:
            break
    seq = [frames[idx]] + [f for f in frames[idx + 1 :] if f[0] < end]
    out: list[tuple[str, float]] = []
    for i, (ts, name) in enumerate(seq):
        t0 = max(ts, start)
        t1 = seq[i + 1][0] if i + 1 < len(seq) else end
        d = min(t1, end) - t0
        if d > 0.0005:
            out.append((name, d))
    return out


def encode_frames(frames_dir: Path, seq: list[tuple[str, float]], out: Path) -> None:
    total = sum(d for _, d in seq)
    n_out = max(1, int(round(total * FPS)))
    cmd = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "image2pipe",
        "-framerate",
        str(FPS),
        "-c:v",
        "mjpeg",
        "-i",
        "pipe:0",
        "-vf",
        "format=yuv420p",
        "-an",
        *X264,
        str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)  # noqa: S603
    assert proc.stdin is not None
    j, end_j = 0, seq[0][1]
    last_name, data = None, b""
    for k in range(n_out):
        t = k / FPS + 1e-6
        while t >= end_j and j < len(seq) - 1:
            j += 1
            end_j += seq[j][1]
        if seq[j][0] != last_name:
            last_name = seq[j][0]
            data = (frames_dir / last_name).read_bytes()
        proc.stdin.write(data)
    proc.stdin.close()
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise SystemExit(f"ffmpeg failed for {out.name}: {err[-2000:]}")


def encode_card(png: Path, out: Path, seconds: float, fade_in: bool, fade_out: bool) -> None:
    vf = [f"fps={FPS}"]
    if fade_in:
        vf.append("fade=t=in:st=0:d=0.5")
    if fade_out:
        vf.append(f"fade=t=out:st={seconds - 0.5:.2f}:d=0.5")
    vf.append("format=yuv420p")
    run(
        [
            FFMPEG,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-t",
            f"{seconds}",
            "-i",
            str(png),
            "-vf",
            ",".join(vf),
            "-an",
            *X264,
            str(out),
        ]
    )


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def assemble(work: Path, out: Path, shots_def: list[tuple[str, str]], narration: list[float]) -> Path:
    frames = [tuple(f) for f in json.loads((work / "frames.json").read_text(encoding="utf-8"))]
    shots = json.loads((work / "shots.json").read_text(encoding="utf-8"))
    seg_dir = work / "segments"
    seg_dir.mkdir(exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    title_png, end_png = work / "title.png", work / "end.png"
    render_html_png(title_card_html(), title_png, 1920, 1080)
    render_html_png(end_card_html(), end_png, 1920, 1080)
    segments: list[tuple[Path, Path | None, int | None]] = []
    encode_card(title_png, seg_dir / "00-title.mp4", CARD_SECONDS, True, True)
    segments.append((seg_dir / "00-title.mp4", None, None))
    for shot in shots:
        i = shot["index"]
        seq = frames_for_shot(frames, shot["start"], shot["end"])
        seg = seg_dir / f"{i:02d}-shot.mp4"
        encode_frames(work / "frames", seq, seg)
        segments.append((seg, work / "vo" / f"vo{i}.mp3", i))
        print(f"  segment {i}: {len(seq)} frames -> {media_duration(seg):.2f}s")
    encode_card(end_png, seg_dir / "99-end.mp4", CARD_SECONDS, True, False)
    segments.append((seg_dir / "99-end.mp4", None, None))

    wavs: list[Path] = []
    t = 0.0
    srt: list[str] = []
    timeline: list[dict] = [{"shot": 0, "title": "Title card", "start": 0.0, "end": CARD_SECONDS}]
    for seg, vo, idx in segments:
        length = media_duration(seg)
        wav = seg.with_suffix(".wav")
        if vo is None:
            run(
                [
                    FFMPEG,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=48000:cl=stereo",
                    "-t",
                    f"{length:.3f}",
                    "-c:a",
                    "pcm_s16le",
                    str(wav),
                ]
            )
        else:
            delay = int(NARRATION_LEAD * 1000)
            run(
                [
                    FFMPEG,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(vo),
                    "-af",
                    f"adelay={delay}|{delay},apad",
                    "-t",
                    f"{length:.3f}",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-c:a",
                    "pcm_s16le",
                    str(wav),
                ]
            )
            nd = narration[idx - 1]
            srt.append(
                f"{len(srt) + 1}\n{srt_time(t + NARRATION_LEAD)} --> {srt_time(t + NARRATION_LEAD + nd)}\n{shots_def[idx - 1][1]}\n"
            )
            timeline.append(
                {
                    "shot": idx,
                    "title": shots_def[idx - 1][0],
                    "start": round(t, 2),
                    "end": round(t + length, 2),
                    "narration_s": round(nd, 2),
                }
            )
        wavs.append(wav)
        t += length
    timeline.append(
        {"shot": len(shots_def) + 1, "title": "End card", "start": round(t - CARD_SECONDS, 2), "end": round(t, 2)}
    )

    alist = work / "audio.txt"
    alist.write_text("".join(f"file '{w.as_posix()}'\n" for w in wavs), encoding="utf-8")
    audio = work / "audio.wav"
    run(
        [
            FFMPEG,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(alist),
            "-c",
            "copy",
            str(audio),
        ]
    )
    vlist = work / "video.txt"
    vlist.write_text("".join(f"file '{s.as_posix()}'\n" for s, _, _ in segments), encoding="utf-8")
    final = out / "autopilot-demo.mp4"
    run(
        [
            FFMPEG,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(vlist),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(final),
        ]
    )
    (out / "autopilot-demo.srt").write_text("\n".join(srt), encoding="utf-8")
    (out / "shots.json").write_text(json.dumps(timeline, indent=1), encoding="utf-8")
    bg = next(
        (
            p
            for p in (
                out / "stills" / "09-journey-card.png",
                out / "stills" / "03-mr1.png",
                out / "stills" / "01-readme.png",
            )
            if p.exists()
        ),
        None,
    )
    if bg:
        render_html_png(thumbnail_html(bg), out / "autopilot-demo-thumb.png", 1280, 720)
    for png in sorted((out / "stills").glob("*.png")):
        if not png.name.endswith("-720p.png"):
            run(
                [
                    FFMPEG,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(png),
                    "-vf",
                    "scale=1280:720:flags=lanczos",
                    str(png.with_name(png.stem + "-720p.png")),
                ]
            )
    return final


# ------------------------------------------------------------------------------------------------------------
# 4. verification
# ------------------------------------------------------------------------------------------------------------


def verify(final: Path, work: Path) -> dict:
    probe = json.loads(
        run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(final)]).stdout
    )
    dur = float(probe["format"]["duration"])
    size = int(probe["format"]["size"])
    kinds = {s["codec_type"]: s for s in probe["streams"]}
    assert "audio" in kinds, "no audio stream"
    assert "video" in kinds, "no video stream"
    assert dur <= HARD_CAP, f"too long: {dur:.1f}s > {HARD_CAP}s"
    samples = work / "samples"
    samples.mkdir(exist_ok=True)
    report = []
    for t in [1.5, 5, 25, 50, 80, 110, 140, dur - 8, dur - 1.5]:
        if t < 0 or t > dur:
            continue
        png = samples / f"t{int(t):03d}.png"
        run(
            [
                FFMPEG,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{t:.2f}",
                "-i",
                str(final),
                "-frames:v",
                "1",
                str(png),
            ]
        )
        stats = run(
            [
                FFMPEG,
                "-hide_banner",
                "-ss",
                f"{t:.2f}",
                "-i",
                str(final),
                "-frames:v",
                "1",
                "-vf",
                "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                "-f",
                "null",
                "-",
            ]
        ).stderr
        yavg = next((float(line.split("=")[-1]) for line in stats.splitlines() if "YAVG" in line), 0.0)
        report.append({"t": round(t, 1), "yavg": yavg})
        assert yavg > 6, f"frame at {t:.1f}s is black (YAVG={yavg})"  # dark UIs (status page) sit around 15
    vol = run([FFMPEG, "-hide_banner", "-i", str(final), "-af", "volumedetect", "-vn", "-f", "null", "-"]).stderr
    mean_vol = next((line.split("mean_volume:")[1].strip() for line in vol.splitlines() if "mean_volume" in line), "?")
    result = {
        "duration_s": round(dur, 2),
        "size_mb": round(size / 1048576, 1),
        "video": f"{kinds['video']['codec_name']} {kinds['video']['width']}x{kinds['video']['height']} {kinds['video'].get('r_frame_rate')}",
        "audio": f"{kinds['audio']['codec_name']} {kinds['audio'].get('sample_rate')} Hz",
        "mean_volume": mean_vol,
        "frames": report,
    }
    print(json.dumps(result, indent=1))
    (final.parent / "verify.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--voice", default="en-US-AndrewNeural")
    ap.add_argument("--rate", default="-4%")
    ap.add_argument("--out", default=str(REPO / "video"))
    ap.add_argument("--work", default=str(Path(tempfile.gettempdir()) / "autopilot-video"))
    ap.add_argument("--step", choices=["all", "narrate", "record", "assemble", "verify"], default="all")
    ap.add_argument(
        "--duo-issue", default=None, help="hackathon-project issue URL with the flow's plan note; adds the Duo shot"
    )
    ap.add_argument(
        "--duo-mr", default=None, help="hackathon-project MR URL opened by the flow (second half of the Duo shot)"
    )
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    shots_def = list(SHOTS)
    if a.duo_issue:
        shots_def.insert(2, DUO_SHOT)
    work, out = Path(a.work), Path(a.out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    print(f"ffmpeg: {FFMPEG}\nwork:   {work}\nout:    {out}\nshots:  {len(shots_def)}")
    t0 = time.time()
    narration: list[float] = []
    if a.step in ("all", "narrate", "record", "assemble"):
        print("[1/4] narration")
        narration = narrate(work, shots_def, a.voice, a.rate)
    if a.step in ("all", "record"):
        print("[2/4] recording")
        record(work, out, shots_def, narration, (a.duo_issue, a.duo_mr) if a.duo_issue else None, a.headed)
    final = out / "autopilot-demo.mp4"
    if a.step in ("all", "assemble"):
        print("[3/4] assembling")
        final = assemble(work, out, shots_def, narration)
    if a.step in ("all", "assemble", "verify"):
        print("[4/4] verifying")
        verify(final, work)
    print(f"done in {time.time() - t0:.0f}s -> {final}")


if __name__ == "__main__":
    main()
