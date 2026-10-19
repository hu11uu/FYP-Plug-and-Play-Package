"""Usage: python sync.py <path-to-FYP-project> <path-to-plugplay-package>

Copy the project's algorithm files into the package and refresh the integrity manifest."""
import hashlib, json, shutil, sys
from pathlib import Path
project, package = Path(sys.argv[1]), Path(sys.argv[2])
manifest_path = package / "source-manifest.json"
manifest = json.loads(manifest_path.read_text())
files = {}
# Only the algorithm's own files: app.py, helper/*.py, pages/*.py (never a venv or tests folder).
sources = [project / "app.py"] + sorted((project / "helper").glob("*.py")) + sorted((project / "pages").glob("*.py"))
for src in sources:
    if not src.is_file(): sys.exit(f"Missing {src}: point sync.py at the FYP-project folder")
    rel = "FYP-project/" + src.relative_to(project).as_posix()
    dst = package / "vendor" / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    files[rel] = hashlib.sha256(dst.read_bytes()).hexdigest()
changed = [k for k in files if manifest["files"].get(k) != files[k]]
manifest["files"] = files
manifest.pop("archive_name", None); manifest.pop("archive_sha256", None)
manifest["source"] = "FYP-project (algorithm files copied verbatim by sync.py)"
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
print(len(files), "files;", "changed/added:", changed)
