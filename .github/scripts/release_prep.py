"""
Paruošia naują versiją GitHub Actions'e:
  - nustato versijos numerį (pagal kodą arba paskutinį v* tag'ą),
  - įrašo jį į updater.py (APP_VERSION),
  - sudaro pakeitimų aprašą (laukelis -> KAS_NAUJO.md -> commit'ai),
  - išvalo KAS_NAUJO.md,
  - rezultatus įrašo į GITHUB_OUTPUT.

Naudojimas: python .github/scripts/release_prep.py --bump patch --notes-out notes.md
Pakeitimų tekstas iš laukelio perduodamas per aplinkos kintamąjį RELEASE_NOTES.
"""
import os
import re
import sys
import argparse
import subprocess

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VERSION_FILE = os.path.join(ROOT, "updater.py")
NOTES_FILE = os.path.join(ROOT, "KAS_NAUJO.md")
VERSION_RE = re.compile(r'^APP_VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)"', re.M)
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

NOTES_TEMPLATE = """# Kas naujo

<!--
Čia paprastais žodžiais rašyk, kas pasikeitė programoje – po vieną eilutę su „- “.
Išleidus naują versiją šis tekstas automatiškai tampa pakeitimų sąrašu, o failas išvalomas.
-->
"""


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def read_code_version():
    with open(VERSION_FILE, encoding="utf-8") as f:
        m = VERSION_RE.search(f.read())
    if not m:
        raise SystemExit("KLAIDA: updater.py nerasta eilutė APP_VERSION = \"X.Y.Z\"")
    return tuple(int(x) for x in m.groups())


def write_code_version(v) -> None:
    with open(VERSION_FILE, encoding="utf-8") as f:
        text = f.read()
    new_text, n = VERSION_RE.subn(f'APP_VERSION = "{v[0]}.{v[1]}.{v[2]}"', text, count=1)
    if n != 1:
        raise SystemExit("KLAIDA: nepavyko įrašyti versijos į updater.py")
    with open(VERSION_FILE, "w", encoding="utf-8", newline="") as f:
        f.write(new_text)


def latest_tag():
    tags = []
    for t in git("tag", "-l", "v*").splitlines():
        m = TAG_RE.match(t.strip())
        if m:
            tags.append(tuple(int(x) for x in m.groups()))
    return max(tags) if tags else None


def bump(v, part: str):
    if part == "major":
        return (v[0] + 1, 0, 0)
    if part == "minor":
        return (v[0], v[1] + 1, 0)
    return (v[0], v[1], v[2] + 1)


def decide_version(code_v, tag_v, part: str):
    if tag_v is None or code_v > tag_v:
        return code_v
    return bump(tag_v, part)


def notes_from_file() -> str:
    if not os.path.exists(NOTES_FILE):
        return ""
    with open(NOTES_FILE, encoding="utf-8-sig") as f:
        text = f.read()
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    lines = [l for l in text.splitlines() if not re.match(r"^\s*#\s*Kas naujo\s*$", l, re.I)]
    return "\n".join(lines).strip()


def notes_from_commits(tag_v) -> str:
    rng = [f"v{tag_v[0]}.{tag_v[1]}.{tag_v[2]}..HEAD"] if tag_v else ["-n", "30"]
    try:
        out = git("log", *rng, "--no-merges", "--pretty=format:%s")
    except subprocess.CalledProcessError:
        return ""
    items = [s.strip() for s in out.splitlines()
             if s.strip() and not re.match(r"^Versija v\d", s.strip())]
    return "\n".join(f"- {s}" for s in items)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bump", choices=["patch", "minor", "major"], default="patch")
    ap.add_argument("--notes-out", required=True)
    a = ap.parse_args()

    code_v = read_code_version()
    tag_v = latest_tag()
    new_v = decide_version(code_v, tag_v, a.bump)
    ver = f"{new_v[0]}.{new_v[1]}.{new_v[2]}"
    tag = f"v{ver}"

    # Ta pati versija du kartus neišleidžiama
    if git("tag", "-l", tag):
        raise SystemExit(f"KLAIDA: tag'as {tag} jau egzistuoja")
    if git("ls-remote", "--tags", "origin", f"refs/tags/{tag}"):
        raise SystemExit(f"KLAIDA: tag'as {tag} jau yra GitHub'e")

    print(f"Kodo versija: {'.'.join(map(str, code_v))}, paskutinis tag'as: "
          f"{'v' + '.'.join(map(str, tag_v)) if tag_v else '(nėra)'} -> nauja versija {tag}")

    notes = (os.environ.get("RELEASE_NOTES") or "").strip()
    source = "laukelio"
    if not notes:
        notes, source = notes_from_file(), "KAS_NAUJO.md"
    if not notes:
        notes, source = notes_from_commits(tag_v), "commit'ų"
    if not notes:
        notes, source = "- Smulkūs patobulinimai.", "numatytasis"
    print(f"Pakeitimų aprašas paimtas iš: {source}")

    with open(a.notes_out, "w", encoding="utf-8") as f:
        f.write(notes.strip() + "\n")

    write_code_version(new_v)
    with open(NOTES_FILE, "w", encoding="utf-8", newline="") as f:
        f.write(NOTES_TEMPLATE)

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"version={ver}\n")
            f.write(f"tag={tag}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
