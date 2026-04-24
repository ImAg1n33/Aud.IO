import re
import subprocess
import sys
from pathlib import Path


EXCLUDED_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".7z",
    ".mp3",
    ".wav",
    ".mp4",
    ".mov",
    ".exe",
    ".dll",
    ".so",
    ".bin",
}

EXCLUDED_FILES = {
    "backend/.env.example",
}

PATTERNS = [
    ("deepseek-or-openai-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("netease-music_u", re.compile(r"\bMUSIC_U\s*=\s*[A-Za-z0-9]{40,}")),
    ("netease-cookie", re.compile(r"\bNETEASE_COOKIE\s*=\s*.+")),
    (
        "generic-credential-assignment",
        re.compile(r"\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
    ),
]


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    files = []
    for line in result.stdout.splitlines():
        rel = line.strip().replace("\\", "/")
        if not rel:
            continue
        if rel in EXCLUDED_FILES:
            continue
        path = Path(rel)
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        files.append(path)
    return files


def scan_file(path: Path) -> list[str]:
    findings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return findings
    except OSError:
        return findings

    for i, line in enumerate(text.splitlines(), start=1):
        for label, pattern in PATTERNS:
            if pattern.search(line):
                findings.append(f"{path.as_posix()}:{i} [{label}] {line[:120]}")
    return findings


def main() -> int:
    all_findings: list[str] = []
    for f in tracked_files():
        all_findings.extend(scan_file(f))

    if all_findings:
        print("Potential secrets detected in tracked files:")
        for item in all_findings:
            print(f"- {item}")
        print("\nResolve findings before commit/push.")
        return 1

    print("No obvious secrets detected in tracked files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
