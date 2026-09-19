"""Loader категорий: categories/*/category.py -> (INFO, router, module)."""
import importlib.util
from pathlib import Path

from aiogram import Router
from core.base import CategoryInfo


def _load_module(cat_id: str, path: Path):
    spec = importlib.util.spec_from_file_location(f"categories.{cat_id}.category", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"no spec for {cat_id}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_categories(root: Path | str = "categories"):
    root = Path(root)
    loaded = []
    if not root.exists():
        return loaded
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if d.name.startswith("_") or d.name.startswith("."):
            continue
        cat_file = d / "category.py"
        if not cat_file.exists():
            continue
        try:
            mod = _load_module(d.name, cat_file)
        except Exception as e:
            print(f"[loader] skip {d.name}: {e}")
            continue
        raw = getattr(mod, "INFO", None)
        router = getattr(mod, "router", None)
        if not isinstance(raw, dict) or "id" not in raw or "title" not in raw:
            print(f"[loader] skip {d.name}: no INFO")
            continue
        if not isinstance(router, Router):
            print(f"[loader] skip {d.name}: no router")
            continue
        info = CategoryInfo(id=raw["id"], title=raw["title"], desc=raw.get("desc", ""))
        loaded.append({"info": info, "router": router, "module": mod})
    return loaded
