"""Prepares the Cloudflare Workers bundle (wrangler.jsonc runs this before every dev/deploy).

cloudflare/.cf-build/src     worker.py + the shop package (Python code, templates, schema): the Worker
cloudflare/.cf-build/assets  shop/static, served by Cloudflare at /static/... without running any code
"""
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHOP = HERE.parent / "shop"
OUT = HERE / ".cf-build"

shutil.rmtree(OUT, ignore_errors=True)
(OUT / "src").mkdir(parents=True)
shutil.copy2(HERE / "worker.py", OUT / "src" / "worker.py")
shutil.copytree(SHOP, OUT / "src" / "shop", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "static"))
shutil.copytree(SHOP / "static", OUT / "assets" / "static")
print("Built cloudflare/.cf-build")
