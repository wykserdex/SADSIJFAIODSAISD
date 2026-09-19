"""База мультитула: контракт категории."""
from dataclasses import dataclass


@dataclass
class CategoryInfo:
    id: str
    title: str
    desc: str = ""
