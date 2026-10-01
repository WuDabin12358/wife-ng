"""Fetch the separate Baritone dependency and verify its pinned SHA-256."""
import hashlib
import os
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
NAME = "baritone-api-fabric-1.6.3.jar"
SHA256 = "79d4c7d359f65320a324c46e9c4af7f550df260117afa0036caa12f6676566d8"
URL = "https://github.com/cabaletta/baritone/releases/download/v1.6.3/" + NAME

def main():
    target = ROOT / "libs" / NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == SHA256:
        print("Baritone verified:", target.name)
        return
    temporary = target.with_suffix(".download")
    try:
        with urllib.request.urlopen(URL, timeout=120) as response:
            temporary.write_bytes(response.read())
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != SHA256:
            raise RuntimeError("Baritone checksum mismatch; download rejected")
        os.replace(temporary, target)
        print("Baritone downloaded and verified:", target.name)
    finally:
        temporary.unlink(missing_ok=True)

if __name__ == "__main__":
    main()
