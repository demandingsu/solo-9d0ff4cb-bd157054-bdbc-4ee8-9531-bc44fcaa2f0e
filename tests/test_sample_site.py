import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1] / "sample_site"
INTENTIONALLY_MISSING = {
    "/detail/d-004-correction.html",
    "/detail/m-002-correction.html",
}
CATALOG_PAGES = [
    "index.html",
    "notices/page-1.html",
    "notices/page-2.html",
    "notices/page-3.html",
    "mixed/page-1.html",
    "mixed/page-2.html",
    "verify/page-1.html",
]


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for key in ("href", "src"):
            if key in attrs:
                self.links.append(attrs[key])


class SampleSiteTests(unittest.TestCase):
    def parse_links(self, relative_path):
        parser = LinkParser()
        parser.feed((ROOT / relative_path).read_text(encoding="utf-8"))
        return parser.links

    def test_local_links_exist_except_intentional_404(self):
        for page in CATALOG_PAGES:
            with self.subTest(page=page):
                for link in self.parse_links(page):
                    if link.startswith(("http://", "https://", "mailto:", "#")):
                        continue
                    absolute = urljoin(f"/{page}", link)
                    path = urlsplit(absolute).path
                    if path in INTENTIONALLY_MISSING:
                        continue
                    self.assertTrue((ROOT / path.lstrip("/")).exists(), f"{page} -> {path}")

    def test_pagination_contains_all_three_pages(self):
        first_page_links = set(self.parse_links("notices/page-1.html"))
        self.assertIn("page-2.html", first_page_links)
        self.assertIn("page-3.html", first_page_links)

    def test_duplicate_announcement_appears_across_pages(self):
        page_two = "\n".join(self.parse_links("notices/page-2.html"))
        page_three = "\n".join(self.parse_links("notices/page-3.html"))
        self.assertIn("d-001-original.html", page_two)
        self.assertIn("d-001-original.html", page_three)

    def test_mixed_catalog_pagination_and_repeated_link(self):
        first = "\n".join(self.parse_links("mixed/page-1.html"))
        second = "\n".join(self.parse_links("mixed/page-2.html"))
        self.assertIn("page-2.html", first)
        # The generic-title cancel link repeats across pages but stays unnumbered
        # in the catalog; its project number only exists in the detail page.
        self.assertIn("m-001-cancel.html", first)
        self.assertIn("m-001-cancel.html", second)

    def test_mixed_catalog_keeps_project_numbers_out_of_listing(self):
        for page in ("mixed/page-1.html", "mixed/page-2.html"):
            with self.subTest(page=page):
                text = (ROOT / page).read_text(encoding="utf-8")
                # GSB-2026-006 records must only carry generic titles in the catalog.
                self.assertNotIn("GSB-2026-006", text)
        # The detail pages still declare the project number.
        detail = (ROOT / "detail/m-001-cancel.html").read_text(encoding="utf-8")
        self.assertIn("GSB-2026-006", detail)

    def test_verify_catalog_links_to_intentionally_missing_detail(self):
        links = "\n".join(self.parse_links("verify/page-1.html"))
        self.assertIn("m-002-correction.html", links)
        self.assertFalse((ROOT / "detail/m-002-correction.html").exists())


if __name__ == "__main__":
    unittest.main()
