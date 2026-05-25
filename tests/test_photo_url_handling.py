import io
import os
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError

from data_loader import SytistDataLoader
from export_service import ExportService
from models import CartItem, Order, PhotoPath


class _PrintingServiceStub:
    def determine_folder(self, product: str) -> str:
        return "4x6"


class PhotoUrlHandlingTests(unittest.TestCase):
    def test_sql_loader_preserves_full_and_web_photo_filenames(self):
        sql = """
CREATE TABLE `ms_orders` (
  `order_id` int(11) NOT NULL,
  `order_first_name` varchar(255) NOT NULL,
  `order_last_name` varchar(255) NOT NULL,
  `order_email` varchar(255) NOT NULL,
  `order_total` varchar(20) NOT NULL,
  `order_status` int(11) NOT NULL
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3;
INSERT INTO `ms_orders` VALUES
(1,'Jane','Doe','jane@example.com','12.00',1);

CREATE TABLE `ms_cart` (
  `cart_order` int(11) NOT NULL,
  `cart_product_name` varchar(255) NOT NULL,
  `cart_qty` varchar(20) NOT NULL,
  `cart_price` varchar(20) NOT NULL,
  `cart_pic_org` varchar(255) NOT NULL,
  `cart_pic_id` int(11) NOT NULL
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3;
INSERT INTO `ms_cart` VALUES
(1,'4x6','1','1.00','IMG_1.jpg',101);

CREATE TABLE `ms_order_status` (
  `status_id` int(11) NOT NULL,
  `status_name` varchar(255) NOT NULL,
  `status_descr` text NOT NULL,
  `status_show_order` int(11) NOT NULL
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3;
INSERT INTO `ms_order_status` VALUES
(1,'New','',1);

CREATE TABLE `ms_photos` (
  `pic_id` int(11) NOT NULL,
  `pic_folder` varchar(255) NOT NULL,
  `pic_full` text NOT NULL,
  `pic_pic` text NOT NULL,
  `pic_large` text NOT NULL
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3;
INSERT INTO `ms_photos` VALUES
(101,'2026/01/01/00','original_abc_IMG_1.jpg','small_abc_IMG_1.jpg','large_abc_IMG_1.jpg');
"""
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as tmp:
            tmp.write(sql)
            sql_path = tmp.name
        self.addCleanup(lambda: os.path.exists(sql_path) and os.remove(sql_path))

        loader = SytistDataLoader()
        _, _, photo_paths, _ = loader.load_sql_dump(sql_path)

        photo = photo_paths["101"]
        self.assertEqual(photo.hashed_file, "original_abc_IMG_1.jpg")
        self.assertEqual(photo.web_file, "small_abc_IMG_1.jpg")
        self.assertEqual(photo.large_file, "large_abc_IMG_1.jpg")

    def test_downloads_fallback_to_alternate_photo_url_when_primary_fails(self):
        service = ExportService(_PrintingServiceStub())
        order = Order(id="1", name="Jane Doe", first="Jane", last="Doe", email="", total="0")
        item = CartItem(order_id="1", product="4x6", qty="1", price="1.00", file="IMG_1.jpg", pic_id="101")
        photo = PhotoPath(
            folder="2026/01/01/00",
            hashed_file="original_abc_IMG_1.jpg",
            web_file="small_abc_IMG_1.jpg",
            large_file="large_abc_IMG_1.jpg",
        )
        tasks = service.build_download_tasks(
            selected_orders=[order],
            cart_items=[item],
            photo_paths={"101": photo},
            domain="https://example.test",
        )
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].urls[0], "https://example.test/sy-photos/2026/01/01/00/original_abc_IMG_1.jpg")
        self.assertEqual(tasks[0].urls[1], "https://example.test/sy-photos/2026/01/01/00/large_abc_IMG_1.jpg")
        self.assertEqual(tasks[0].urls[2], "https://example.test/sy-photos/2026/01/01/00/small_abc_IMG_1.jpg")

        attempted = []

        def fake_urlopen(request):
            attempted.append(request.full_url)
            if request.full_url.endswith("original_abc_IMG_1.jpg"):
                raise HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=None)
            return io.BytesIO(b"image-bytes")

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                service.process_downloads(tasks=tasks, base_dir=tmpdir)

            output_file = os.path.join(tmpdir, "4x6", "1_Doe_Jane_IMG_1.jpg")
            self.assertTrue(os.path.exists(output_file))
            with open(output_file, "rb") as fh:
                self.assertEqual(fh.read(), b"image-bytes")

        self.assertEqual(
            attempted[:2],
            [
                "https://example.test/sy-photos/2026/01/01/00/original_abc_IMG_1.jpg",
                "https://example.test/sy-photos/2026/01/01/00/large_abc_IMG_1.jpg",
            ],
        )


if __name__ == "__main__":
    unittest.main()
