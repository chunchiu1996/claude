"""Prepares the Cloudflare Workers bundle (wrangler.jsonc runs this before every dev/deploy).

.cf-build/src     worker.py + the shop package (Python code, templates, schema): the Worker itself
.cf-build/assets  shop/static, served by Cloudflare at /static/... without running any code
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".cf-build"

shutil.rmtree(OUT, ignore_errors=True)
(OUT / "src").mkdir(parents=True)
shutil.copy2(ROOT / "worker.py", OUT / "src" / "worker.py")
shutil.copytree(ROOT / "shop", OUT / "src" / "shop", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "static"))
shutil.copytree(ROOT / "shop" / "static", OUT / "assets" / "static")
print(f"Built {OUT.relative_to(ROOT)}")
