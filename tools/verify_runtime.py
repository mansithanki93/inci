import hashlib
import pathlib
import sys


minimum = (3, 12)
maximum = (3, 15)
if not minimum <= sys.version_info[:2] < maximum:
    raise SystemExit(
        "Inci Tennis v1 requires Python >=3.12,<3.15; got "
        f"{sys.version_info.major}.{sys.version_info.minor}"
    )
executable = pathlib.Path(sys.executable).resolve(strict=True)
digest = hashlib.sha256(executable.read_bytes()).hexdigest()
print(f"{executable} {sys.version.split()[0]} sha256={digest}")
