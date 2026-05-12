import subprocess
import sys
from pathlib import Path


def main():
    out_path = Path("data") / "test_report_utf8_manual.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pytest", "-q"]
    with out_path.open("w", encoding="utf-8", errors="replace") as f:
        f.write("Starting pytest...\n")
        f.flush()
        proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT)
        ret = proc.wait()
        f.write(f"\nPYTEST_EXIT:{ret}\n")


if __name__ == '__main__':
    main()
