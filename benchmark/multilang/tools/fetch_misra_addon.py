import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
FILES = ("misra.py", "cppcheckdata.py", "misra_9.py")

def installed_version() -> str | None:
    exe = shutil.which("cppcheck")
    if not exe:
        return None
    out = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", out)
    return m.group(1) if m else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", help="cppcheck tag (default: the installed version)")
    a = ap.parse_args()
    ver = a.version or installed_version()
    if not ver:
        sys.exit("cppcheck not found; give the tag with --version (e.g. 2.20.0)")
    if ver.count(".") == 1:
        ver += ".0"
    dest = HERE / "cppcheck-addons"
    dest.mkdir(exist_ok=True)
    for f in FILES:
        url = f"https://raw.githubusercontent.com/danmar/cppcheck/{ver}/addons/{f}"
        print(f"downloading {url}")
        with urllib.request.urlopen(url, timeout=60) as r:
            (dest / f).write_bytes(r.read())
    print(f"done: {dest} (cppcheck {ver}, GPL-3.0)")

if __name__ == "__main__":
    main()
