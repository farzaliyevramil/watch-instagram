#!/usr/bin/env python3
"""Prepare an Instagram Reel/post (or any yt-dlp URL, or a local video) for Claude to "watch".

Downloads the video, picks key frames (one per shot plus even fill, first and last frame
always included), builds a contact sheet, transcribes the audio locally with whisper.cpp,
and writes report.md with the caption, account, counts, frame list and timestamped
transcript. The report is also printed to stdout.

Usage: watch.py URL_OR_FILE [--lang az] [--max-frames 16] [--out DIR]
                            [--cookies-from-browser chrome] [--no-transcript]
"""

import argparse
import datetime
import glob
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
VENV_YTDLP = SKILL_DIR / ".venv/bin/yt-dlp"
DEFAULT_MODEL = Path.home() / ".cache/whisper-cpp-models/ggml-large-v3-q5_0.bin"
DEFAULT_ROOT = Path.home() / "Downloads/watch-instagram"
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".m4v"}
LOGIN_HINTS = ("login required", "log in", "rate-limit", "rate limit", "not available",
               "private", "cookies", "401", "403")


def die(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def run(cmd, check=True):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode != 0:
        tail = "\n".join((p.stderr or p.stdout).strip().splitlines()[-15:])
        die(f"{cmd[0]} failed (exit {p.returncode}):\n{tail}")
    return p


def need(tool, install):
    if not shutil.which(tool):
        die(f"{tool} not found. Install it with: {install}")


def fmt_t(seconds):
    m, s = divmod(max(seconds, 0), 60)
    return f"{int(m):02d}:{s:04.1f}"


def fmt_n(n):
    if n is None:
        return None
    for div, suf in ((1e6, "M"), (1e3, "k")):
        if n >= div:
            return f"{n / div:.1f}{suf}"
    return str(n)


# ---------------------------------------------------------------- download

def workdir_for(source, root):
    m = re.search(r"/(?:reels?|p|tv|stories/[^/]+)/([A-Za-z0-9_-]+)", source)
    if m:
        return root / m.group(1)
    return root / ("link-" + hashlib.sha1(source.encode()).hexdigest()[:10])


def download(url, media_dir, cookies_browser):
    existing = [p for p in media_dir.glob("*") if p.suffix.lower() in VIDEO_EXTS]
    if existing:
        print(f"(using already-downloaded media in {media_dir})", file=sys.stderr)
        return
    if VENV_YTDLP.exists():
        ytdlp = str(VENV_YTDLP)
    else:
        need("yt-dlp", f"bash {SKILL_DIR / 'scripts/setup.sh'}")
        ytdlp = "yt-dlp"
    media_dir.mkdir(parents=True, exist_ok=True)
    cmd = [ytdlp, "--no-progress", "--write-info-json",
           "-f", "bv*+ba/b", "--merge-output-format", "mp4",
           "-P", str(media_dir), "-o", "%(playlist_index|1)s-%(id)s.%(ext)s"]
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    p = run(cmd + [url], check=False)
    if p.returncode != 0:
        err = "\n".join(p.stderr.strip().splitlines()[-8:])
        low = err.lower()
        if "no video" in low:
            die(f"This post has no video (photo-only post).\n{err}", 3)
        if not cookies_browser and any(h in low for h in LOGIN_HINTS):
            die("Instagram refused the anonymous download. Either the post needs a login "
                "(private account, story, age-restricted, rate limit) or the link is wrong or "
                "deleted; Instagram answers both the same way. It may work with the user's "
                "signed-in browser session via --cookies-from-browser chrome, but ask the user "
                "first.\n" + err, 2)
        die(f"yt-dlp could not download this link:\n{err}")


def load_infos(media_dir):
    post, items = None, []
    for f in sorted(media_dir.glob("*.info.json")):
        try:
            info = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if info.get("_type") == "playlist":
            post = info
        else:
            items.append(info)
    items.sort(key=lambda i: i.get("playlist_index") or 1)
    return post, items


def media_file_for(media_dir, info):
    for p in sorted(media_dir.glob(f"*-{glob.escape(info['id'])}.*")):
        if p.suffix.lower() in VIDEO_EXTS:
            return p
    return None


# ---------------------------------------------------------------- frames

def probe(video):
    p = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(video)])
    data = json.loads(p.stdout)
    vstream = next((s for s in data["streams"] if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in data["streams"])
    duration = float(data["format"].get("duration") or vstream.get("duration") or 0)
    return duration, vstream.get("width"), vstream.get("height"), has_audio


def scene_changes(video, threshold=0.3):
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-an",
             "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo",
             "-f", "null", "-"], check=False)
    return [float(t) for t in re.findall(r"pts_time:([\d.]+)", p.stderr)]


def pick_times(video, duration, max_frames):
    """One frame just after each cut, the first and last frame, even fill between, capped."""
    if duration <= 0:
        return [0.0], 0
    end = max(duration - 0.3, 0.0)
    times = {min(0.3, end), end}
    cuts = scene_changes(video)
    times.update(min(t + 0.3, end) for t in cuts)
    step = max(duration / max_frames, 2.0)
    for k in range(1, int(duration / step) + 1):
        t = k * step
        if t < end and all(abs(t - x) > step / 2 for x in times):
            times.add(t)
    picked = []
    for t in sorted(times):
        if not picked or t - picked[-1] >= 0.8:
            picked.append(t)
    if len(picked) > max_frames:
        idx = [round(i * (len(picked) - 1) / (max_frames - 1)) for i in range(max_frames)]
        picked = [picked[i] for i in sorted(set(idx))]
    return picked, len(cuts)


def extract_frames(video, times, frames_dir):
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("*.jpg"):
        old.unlink()
    out = []
    scale = "scale='if(gt(iw,ih),960,-2)':'if(gt(iw,ih),-2,960)'"
    for i, t in enumerate(times, 1):
        m, s = divmod(int(t), 60)
        path = frames_dir / f"{i:02d}_{m:02d}m{s:02d}s.jpg"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}",
             "-i", str(video), "-frames:v", "1", "-vf", scale, "-q:v", "3", str(path)])
        if path.exists():
            out.append((t, path))
    return out


def contact_sheet(frames, sheet_path):
    """All frames on one image, left→right, top→bottom, so the whole video fits in one look."""
    if not frames:
        return None
    # size from an extracted frame: ffmpeg has already applied any phone rotation there
    _, width, height, _ = probe(frames[0][1])
    if not width or not height:
        return None
    portrait = height >= width
    cols = min(4 if portrait else 3, len(frames))
    tile_w = 300 if portrait else 400
    tile_h = round(tile_w * height / width / 2) * 2
    gap = 8
    inputs, chain, layout = [], "", []
    for i, (_, f) in enumerate(frames):
        inputs += ["-i", str(f)]
        chain += f"[{i}:v]scale={tile_w}:{tile_h},setsar=1[f{i}];"
        layout.append(f"{(i % cols) * (tile_w + gap)}_{(i // cols) * (tile_h + gap)}")
    if len(frames) == 1:
        graph = "[0:v]scale={}:{}[out]".format(tile_w, tile_h)
    else:
        graph = (chain + "".join(f"[f{i}]" for i in range(len(frames))) +
                 f"xstack=inputs={len(frames)}:layout={'|'.join(layout)}:fill=white[out]")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs,
         "-filter_complex", graph, "-map", "[out]", "-frames:v", "1", "-q:v", "3",
         str(sheet_path)])
    return sheet_path if sheet_path.exists() else None


# ---------------------------------------------------------------- transcript

def transcribe(video, out_dir, lang, model):
    need("whisper-cli", "brew install whisper-cpp")
    if not model.exists():
        die(f"Whisper model not found at {model}. Download a ggml model there, or pass --model.")
    wav = out_dir / "audio.wav"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
    base = out_dir / "transcript"
    run(["whisper-cli", "-m", str(model), "-f", str(wav), "-l", lang,
         "-oj", "-otxt", "-of", str(base), "-np"])
    wav.unlink(missing_ok=True)
    data = json.loads(Path(f"{base}.json").read_text(errors="replace"))
    detected = data.get("result", {}).get("language", lang)
    lines = []
    for seg in data.get("transcription", []):
        text = seg.get("text", "").strip()
        if text:
            lines.append(f"[{fmt_t(seg['offsets']['from'] / 1000)}] {text}")
    return detected, lines


# ---------------------------------------------------------------- report

def header_lines(source, workdir, meta, n_videos):
    out = []
    who = meta.get("channel") or meta.get("uploader_id") or meta.get("uploader")
    name = meta.get("uploader")
    title = f"@{who}" if who else "Video"
    if name and name != who:
        title += f" ({name})"
    out.append(f"# {title}")
    out.append(f"Source: {meta.get('webpage_url') or source}")
    out.append(f"Saved in: {workdir}")
    facts = []
    if meta.get("timestamp"):
        posted = datetime.datetime.fromtimestamp(meta["timestamp"], datetime.timezone.utc)
        facts.append("Posted " + posted.strftime("%Y-%m-%d %H:%M UTC"))
    elif meta.get("upload_date"):
        d = meta["upload_date"]
        facts.append(f"Posted {d[:4]}-{d[4:6]}-{d[6:]}")
    for key, label in (("view_count", "views"), ("like_count", "likes"),
                       ("comment_count", "comments")):
        if meta.get(key) is not None:
            facts.append(f"{fmt_n(meta[key])} {label}")
    if n_videos > 1:
        facts.append(f"{n_videos} videos in this post")
    if facts:
        out.append(" · ".join(facts))
    caption = (meta.get("description") or "").strip()
    out += ["", "## Caption", caption if caption else "(no caption)"]
    comments = [c for c in meta.get("comments") or [] if (c.get("text") or "").strip()]
    if comments:
        out += ["", f"## Comments (sample of {len(comments[:10])} that Instagram returned without a login)"]
        for c in comments[:10]:
            text = " ".join(c["text"].split())
            out.append(f"- @{c.get('author') or '?'}: {text[:200]}{'…' if len(text) > 200 else ''}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="Instagram (or other yt-dlp) URL, or a local video file")
    ap.add_argument("--lang", default="auto", help="spoken language for Whisper: auto (default), az, ru, tr, en, ...")
    ap.add_argument("--max-frames", type=int, default=16, help="frames per video (default 16)")
    ap.add_argument("--out", type=Path, default=DEFAULT_ROOT, help=f"output root (default {DEFAULT_ROOT})")
    ap.add_argument("--cookies-from-browser", metavar="BROWSER",
                    help="use the user's signed-in browser session (only with the user's OK)")
    ap.add_argument("--no-transcript", action="store_true", help="skip Whisper")
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL, help="whisper.cpp ggml model path")
    args = ap.parse_args()
    args.max_frames = max(args.max_frames, 2)

    need("ffmpeg", "brew install ffmpeg")
    need("ffprobe", "brew install ffmpeg")

    local = Path(args.source).expanduser()
    if local.is_file():
        workdir = args.out / f"local-{re.sub(r'[^A-Za-z0-9_-]+', '_', local.stem)[:40]}"
        workdir.mkdir(parents=True, exist_ok=True)
        meta, videos = {"webpage_url": str(local)}, [(None, local)]
    else:
        if not re.match(r"https?://", args.source):
            die(f"Not a URL or an existing file: {args.source}")
        workdir = workdir_for(args.source, args.out)
        media_dir = workdir / "media"
        download(args.source, media_dir, args.cookies_from_browser)
        post, items = load_infos(media_dir)
        videos = [(i, media_file_for(media_dir, i)) for i in items]
        videos = [(i, f) for i, f in videos if f]
        if not videos:
            die(f"Download finished but no video file was found in {media_dir}")
        meta = dict(items[0]) if items else {}
        if post:
            meta.update({k: v for k, v in post.items() if v is not None and k != "_type"})

    report = header_lines(args.source, workdir, meta, len(videos))
    for n, (info, video) in enumerate(videos, 1):
        item_dir = workdir if len(videos) == 1 else workdir / f"video{n}"
        item_dir.mkdir(parents=True, exist_ok=True)
        duration, w, h, has_audio = probe(video)
        times, n_cuts = pick_times(video, duration, args.max_frames)
        frames = extract_frames(video, times, item_dir / "frames")
        sheet = contact_sheet(frames, item_dir / "contact_sheet.jpg")

        label = f"Video {n} of {len(videos)}" if len(videos) > 1 else "Video"
        shots = (f"{n_cuts + 1} shots ({n_cuts} cuts detected)" if n_cuts
                 else "1 continuous shot (no cuts detected; frames are evenly spaced)")
        report += ["", f"## {label} — {duration:.1f}s, {w}x{h}, audio: {'yes' if has_audio else 'no'}, {shots}",
                   f"File: {video}"]
        if sheet:
            report.append(f"Contact sheet ({len(frames)} frames, left→right, top→bottom): {sheet}")
        report.append("Frames:")
        report += [f"- {fmt_t(t)}  {p}" for t, p in frames]

        if args.no_transcript:
            report += ["", "Transcript: skipped (--no-transcript)"]
        elif not has_audio:
            report += ["", "Transcript: none (no audio track)"]
        else:
            lang, lines = transcribe(video, item_dir, args.lang, args.model)
            report += ["", f"### Transcript (Whisper large-v3, language: {lang}"
                           f"{' — auto-detected' if args.lang == 'auto' else ''}; machine transcription)"]
            report += lines if lines else ["(no speech detected)"]

    text = "\n".join(report) + "\n"
    (workdir / "report.md").write_text(text)
    print(text)
    print(f"Report saved: {workdir / 'report.md'}")


if __name__ == "__main__":
    main()
