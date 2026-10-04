"""Package the Blender extension without local configuration or caches."""
from pathlib import Path
import tomllib
import zipfile

root = Path(__file__).resolve().parents[1]
source = root / "shortcut_compass"
manifest = tomllib.loads((source / "blender_manifest.toml").read_text(encoding="utf-8"))
destination = root / "dist" / f"{manifest['id']}-{manifest['version']}.zip"
destination.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(source.iterdir()):
        if path.is_file() and (path.suffix in {".py", ".toml", ".md"} or path.name == "LICENSE"):
            archive.write(path, path.name)
print(destination)
