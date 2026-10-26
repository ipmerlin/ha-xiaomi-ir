"""Validate source metadata and build an installable, verified ZIP."""

import ast
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "xiaomi_ir"
manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
assert manifest["domain"] == COMPONENT.name
assert list(manifest) == ["domain", "name"] + sorted(set(manifest) - {"domain", "name"})
assert manifest["config_flow"] is True
assert manifest["requirements"] == ["python-miio==0.5.12"]
for path in COMPONENT.glob("*.py"):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def keys(value, prefix=""):
    result = set()
    for key, item in value.items():
        name = f"{prefix}.{key}"
        result.add(name)
        if isinstance(item, dict):
            result.update(keys(item, name))
    return result


strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
for path in (COMPONENT / "translations").glob("*.json"):
    translated = json.loads(path.read_text(encoding="utf-8"))
    assert keys(translated) == keys(strings), path
    assert all(isinstance(v, dict | str) for v in translated.values())
json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))

sources = sorted(
    path for path in COMPONENT.rglob("*") if path.suffix in {".py", ".json", ".png"}
)
sources += [ROOT / name for name in ("README.md", "LICENSE", "VALIDATION.md")]
destination = ROOT / "dist"
destination.mkdir(exist_ok=True)
archive = destination / f"xiaomi_ir-{manifest['version']}.zip"
with ZipFile(archive, "w", ZIP_DEFLATED) as package:
    for path in sources:
        package.write(path, path.relative_to(ROOT).as_posix())
with ZipFile(archive) as package:
    assert package.testzip() is None
    assert set(package.namelist()) == {
        path.relative_to(ROOT).as_posix() for path in sources
    }
    for path in sources:
        assert package.read(path.relative_to(ROOT).as_posix()) == path.read_bytes()
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(destination / "SHA256SUMS.txt").write_text(
    f"{digest}  {archive.name}\n", encoding="ascii"
)
print(f"Verified {len(sources)} files: {archive.name}")
print(f"SHA256: {digest}")
