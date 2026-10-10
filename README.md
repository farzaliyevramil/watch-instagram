# watch-instagram

A [Claude Code](https://claude.com/claude-code) skill that lets Claude "watch" an Instagram Reel or video post. Paste a link, ask what's in it, and Claude answers from the visuals, the speech and the caption.

Claude can't play video, so the skill turns the video into things Claude can read:

- **Key frames:** one still after every cut, the first and last frame, and even fill in between (up to 16 by default).
- **A contact sheet:** all the frames on one image, so the whole video fits in a single look.
- **A timestamped transcript:** made locally with [whisper.cpp](https://github.com/ggml-org/whisper.cpp). Works for any language Whisper supports, including Azerbaijani, Russian, Turkish and English.
- **Post details:** account, date, likes, views, the caption and a sample of comments.

Claude then lines up what was said with what was on screen and answers your question: a summary, a translation, "is this an ad?", or a hook/offer/CTA breakdown of a competitor's Reel.

It also works for TikTok, Facebook, YouTube Shorts and anything else [yt-dlp](https://github.com/yt-dlp/yt-dlp) supports, and for local video files.

## Install

You need Python 3.10+, `ffmpeg` and `whisper-cli` from whisper.cpp. On macOS with Homebrew:

```bash
brew install ffmpeg whisper-cpp
```

Clone the skill into your personal skills folder and set up its private copy of yt-dlp:

```bash
git clone https://github.com/farzaliyevramil/watch-instagram ~/.claude/skills/watch-instagram
bash ~/.claude/skills/watch-instagram/scripts/setup.sh
```

Download a Whisper model. The default is `large-v3-q5_0`, about 1 GB, which is accurate across languages:

```bash
mkdir -p ~/.cache/whisper-cpp-models
curl -L -o ~/.cache/whisper-cpp-models/ggml-large-v3-q5_0.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-q5_0.bin
```

A smaller model such as `ggml-small.bin` also works. Pass it with `--model`.

Start a new Claude Code session and the skill is available.

## Use

Ask Claude in plain words:

> watch this reel and tell me what it's about https://www.instagram.com/reel/…

> is this an ad? who's it targeting? https://www.instagram.com/p/…

> transcribe and translate this video to English https://www.instagram.com/reel/…

You can also run the script yourself:

```bash
python3 ~/.claude/skills/watch-instagram/scripts/watch.py "https://www.instagram.com/reel/…"
```

| Option | What it does |
|---|---|
| `--lang az` | Force the spoken language. Auto-detect can confuse close languages, such as Azerbaijani and Turkish. |
| `--max-frames 24` | Take more frames from long or fast-cut videos. |
| `--no-transcript` | Skip transcription. |
| `--out DIR` | Output folder. The default is `~/Downloads/watch-instagram/`. |
| `--model PATH` | Use a different whisper.cpp model. |
| `--cookies-from-browser chrome` | Use your signed-in browser session for posts that need a login. |

Each post gets its own folder with the video, the frames, `contact_sheet.jpg`, the transcript and a `report.md`.

## Limits

- **Public posts work without a login.** Private accounts, Stories and some rate-limited requests need `--cookies-from-browser`, which reads your browser's cookies and acts as your Instagram account. The skill tells Claude to ask you before using it.
- **Licensed music is often missing.** Instagram withholds it from viewers who aren't logged in, so some Reels download without audio.
- **Photo-only posts have nothing to watch.** In mixed carousels, only the video slides are downloaded.
- **Transcripts are machine-made.** Check names, brands and numbers against the on-screen text.
- **Instagram changes often.** If downloads start failing, re-run `scripts/setup.sh` to upgrade yt-dlp.

## Why a private yt-dlp?

`setup.sh` installs yt-dlp into the skill's own `.venv` instead of relying on a system copy. Homebrew's Python 3.14 yt-dlp can break on macOS releases older than the one it was built on, with `No module named expat`. A private copy, built on whichever local Python has a working `pyexpat`, avoids that, and upgrading it never touches the rest of your system.

## License

MIT
