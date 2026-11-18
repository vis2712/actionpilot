"""Reading uploaded files: text transcripts (.txt/.md/.vtt/.srt) or audio recordings for Whisper."""

import re
from pathlib import PurePath

TEXT_EXTS = {".txt", ".md", ".vtt", ".srt"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".webm", ".ogg", ".mp4", ".mpeg", ".mpga", ".flac"}

_TIMING = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?[.,]\d{3}\s*-->\s*\d{1,2}:\d{2}(:\d{2})?[.,]\d{3}.*$")
_VTT_VOICE = re.compile(r"^<v\s+([^>]+)>(.*?)(</v>)?$")


def kind_of(filename: str) -> str | None:
    ext = PurePath(filename).suffix.lower()
    if ext in TEXT_EXTS:
        return "text"
    if ext in AUDIO_EXTS:
        return "audio"
    return None


def clean_transcript(raw: str, filename: str = "") -> str:
    """Turns VTT/SRT subtitle files (Zoom, Teams and Meet exports) into plain "Speaker: text" lines."""
    ext = PurePath(filename).suffix.lower()
    if ext not in {".vtt", ".srt"}:
        return raw.replace("\r\n", "\n").strip()
    lines: list[str] = []
    for line in raw.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line or line == "WEBVTT" or line.isdigit() or _TIMING.match(line) or line.startswith("NOTE"):
            continue
        if m := _VTT_VOICE.match(line):
            line = f"{m.group(1).strip()}: {m.group(2).strip()}"
        if lines and lines[-1] == line:  # subtitle files often repeat a line across two cues
            continue
        lines.append(line)
    return "\n".join(lines)


def decode_text(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):  # UTF-16 with a byte-order mark (some Windows exports)
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")  # never fails; old Windows files are usually cp1252/latin-1
