import os
import shutil
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List

from models import CartItem, Order, PhotoPath
from printing_service import PrintingService


def _safe_qty(qty_str: str) -> float:
    """Return the numeric quantity from a string, or 0.0 on any parse error."""
    try:
        return float(qty_str or 0)
    except (ValueError, TypeError):
        return 0.0

@dataclass
class DownloadTask:
    urls: List[str]
    product_folder: str
    prefix: str
    name_base: str
    ext: str
    qty: int


class ExportService:
    def __init__(self, printing_service: PrintingService):
        self.printing_service = printing_service

    @staticmethod
    def _photo_url_candidates(domain: str, photo: PhotoPath) -> List[str]:
        base = domain.rstrip("/")
        filenames = [photo.hashed_file, photo.large_file, photo.web_file]
        urls: List[str] = []
        seen = set()
        for filename in filenames:
            if not filename:
                continue
            url = f"{base}/sy-photos/{photo.folder}/{filename}"
            if url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    def build_download_tasks(
        self,
        selected_orders: List[Order],
        cart_items: List[CartItem],
        photo_paths: Dict[str, PhotoPath],
        domain: str,
    ) -> List[DownloadTask]:
        tasks: List[DownloadTask] = []
        for order in selected_orders:
            items = [item for item in cart_items if item.order_id == order.id and _safe_qty(item.qty) > 0]
            for item in items:
                photo = photo_paths.get(str(item.pic_id))
                if not photo:
                    continue
                urls = self._photo_url_candidates(domain, photo)
                if not urls:
                    continue
                product_folder = self.printing_service.determine_folder(item.product)
                raw_name = item.file or "photo.jpg"
                name_base, ext = os.path.splitext(raw_name)
                if not ext:
                    ext = ".jpg"
                clean_last = order.last.replace(" ", "").replace("?", "")
                clean_first = order.first.replace(" ", "").replace("?", "")
                prefix = f"{order.id}_{clean_last}_{clean_first}"
                tasks.append(DownloadTask(
                    urls=urls,
                    product_folder=product_folder,
                    prefix=prefix,
                    name_base=name_base,
                    ext=ext,
                    qty=max(1, int(_safe_qty(item.qty))),
                ))
        return tasks

    def process_downloads(
        self,
        tasks: List[DownloadTask],
        base_dir: str,
        progress_callback: Callable[[int, int, DownloadTask], None] | None = None,
        error_callback: Callable[[DownloadTask, Exception], None] | None = None,
    ) -> None:
        for index, task in enumerate(tasks, start=1):
            prod_dir = os.path.join(base_dir, task.product_folder)
            os.makedirs(prod_dir, exist_ok=True)
            temp_file = os.path.join(base_dir, "temp_dl" + task.ext)
            try:
                last_exc = None
                for url in task.urls:
                    try:
                        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req) as response, open(temp_file, "wb") as out_file:
                            shutil.copyfileobj(response, out_file)
                        last_exc = None
                        break
                    except Exception as exc:
                        last_exc = exc
                if last_exc is not None:
                    raise last_exc
                for q in range(1, task.qty + 1):
                    final_name = (
                        f"{task.prefix}_{task.name_base}{task.ext}"
                        if q == 1 else
                        f"{task.prefix}_{task.name_base}_{q}{task.ext}"
                    )
                    shutil.copy2(temp_file, os.path.join(prod_dir, final_name))
            except Exception as exc:
                if error_callback:
                    error_callback(task, exc)
            if progress_callback:
                progress_callback(index, len(tasks), task)
        self.cleanup_temp_files(base_dir)

    @staticmethod
    def cleanup_temp_files(base_dir: str) -> None:
        for ext in [".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"]:
            temp_path = os.path.join(base_dir, "temp_dl" + ext)
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
