import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from bs4 import BeautifulSoup

    from tendercheck.models import STATUS_CANCELLED, STATUS_PENDING
    from tendercheck.scraper import (
        Candidate,
        DetailRejection,
        FetchError,
        FetchedPage,
        TenderScraper,
    )
except ModuleNotFoundError:  # requests/beautifulsoup4 are optional for core tests
    TenderScraper = None

ROOT = Path(__file__).resolve().parents[1] / "sample_site"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


@unittest.skipIf(TenderScraper is None, "scraper dependencies are not installed")
class MixedCatalogIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = partial(QuietHandler, directory=str(ROOT))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def check(self, project_no, path, **kwargs):
        return TenderScraper(
            project_no=project_no,
            list_url=f"{self.base}{path}",
            **kwargs,
        ).run()

    def test_unnumbered_cancel_is_confirmed_via_detail_and_status_cancelled(self):
        # Regression: generic-title rows used to be skipped whenever the catalog
        # also contained a row naming the project, hiding the cancellation.
        result = self.check("GSB-2026-006", "/mixed/page-1.html")
        self.assertEqual(result.status, STATUS_CANCELLED)
        self.assertEqual(result.announcement_count, 3)
        titles = {item.title for item in result.announcements}
        self.assertTrue(any("取消公告" in title for title in titles))
        # Every included announcement was attributed through its detail page.
        self.assertTrue(
            all(item.source_note and "详情页确认" in item.source_note for item in result.announcements)
        )

    def test_other_project_generic_rows_are_excluded(self):
        result = self.check("GSB-2026-006", "/mixed/page-1.html")
        project_numbers = {item.project_no for item in result.announcements}
        self.assertEqual(project_numbers, {"GSB-2026-006"})
        self.assertFalse(any("105" in (item.project_no or "") for item in result.announcements))
        # The OTHER-2026-105 row and the unnumbered OTHER detail together must
        # not consume more detail fetches than the five unique mixed links.
        self.assertLessEqual(result.detail_count, 5)

    def test_cross_page_link_is_fetched_once_with_merged_catalog_time(self):
        result = self.check("GSB-2026-006", "/mixed/page-1.html")
        self.assertEqual(len(result.pages), 2)
        # m-001-cancel and m-105-original each appear on both pages.
        self.assertEqual(result.duplicates_skipped, 2)
        cancel = next(item for item in result.announcements if item.kind == "cancellation")
        # Page 2 has no <time> for that row; the publish time comes from page 1
        # thanks to cross-page catalog info merging.
        self.assertEqual(cancel.publish_time.strftime("%Y-%m-%d %H:%M"), "2026-10-28 16:30")

    def test_unnumbered_detail_failure_is_pending_without_definite_status(self):
        result = self.check("GSB-2026-007", "/verify/page-1.html")
        self.assertEqual(result.status, STATUS_PENDING)
        self.assertIsNone(result.deadline)
        self.assertTrue(
            any("详情读取失败" in issue and "无法确认归属" in issue for issue in result.issues)
        )


LISTING_TEMPLATE = """<html><body>
{items}
<nav class="pagination">{pagination}</nav>
</body></html>"""

ITEM_TEMPLATE = """
<article class="notice-item"{data_attr}>
  <h2><a href="{href}">{title}</a></h2>
  <div class="meta">{project_span}<time datetime="{published}">发布时间：{published}</time></div>
</article>"""

DETAIL_TEMPLATE = """<html><body><article class="announcement-detail"{data_attr}>
<h1>{title}</h1>
<div class="publish-meta">项目编号：{project_no} 公告类型：{kind_label}
<time datetime="{published}">发布日期：{published}</time></div>
<section class="content">{content}</section>
</article></body></html>"""


class StubFetcher:
    """In-memory fetcher used to exercise edge cases without extra HTML files."""

    mapper = None

    def __init__(self, pages: dict[str, str], failing: set[str] | None = None):
        self.pages = pages
        self.failing = failing or set()
        self.fetched_urls: list[str] = []

    def fetch(self, url):
        self.fetched_urls.append(url)
        if url in self.failing:
            raise FetchError("HTTP 404")
        return FetchedPage(url=url, soup=BeautifulSoup(self.pages[url], "html.parser"))

    def close(self):
        pass


@unittest.skipIf(TenderScraper is None, "scraper dependencies are not installed")
class MixedCatalogUnitTests(unittest.TestCase):
    BASE = "http://example.test"

    def _listing(self, name, items, pagination=""):
        html_items = []
        for item in items:
            data_attr = (
                f' data-project-no="{item["project_no"]}"' if item.get("project_no") else ""
            )
            project_span = (
                f'<span class="project-no">项目编号：{item["project_no"]}</span>'
                if item.get("project_no")
                else ""
            )
            html_items.append(
                ITEM_TEMPLATE.format(
                    data_attr=data_attr,
                    href=item["href"],
                    title=item["title"],
                    project_span=project_span,
                    published=item["published"],
                )
            )
        self.pages[f"{self.BASE}/{name}"] = LISTING_TEMPLATE.format(
            items="".join(html_items), pagination=pagination
        )

    def _detail(self, path, *, project_no, title, kind_label, published, content, data_no=True):
        data_attr = f' data-project-no="{project_no}"' if data_no else ""
        self.pages[f"{self.BASE}{path}"] = DETAIL_TEMPLATE.format(
            data_attr=data_attr,
            title=title,
            project_no=project_no,
            kind_label=kind_label,
            published=published,
            content=content,
        )

    def setUp(self):
        self.pages: dict[str, str] = {}

    def _scraper(self, project_no, list_path, **kwargs):
        failing = kwargs.pop("failing", None)
        scraper = TenderScraper(
            project_no=project_no,
            list_url=f"{self.BASE}{list_path}",
            fetcher=StubFetcher(self.pages, failing),
            **kwargs,
        )
        return scraper

    def test_detail_budget_cap_leaves_unnumbered_records_pending(self):
        items = [
            {
                "href": "/d/related.html",
                "title": "[GSB-2026-009] 相关项目招标公告",
                "project_no": "GSB-2026-009",
                "published": "2026-10-01T09:00:00+08:00",
            }
        ]
        # Two unnumbered rows, but the cap only allows one detail fetch in total.
        for index in range(2):
            items.append(
                {
                    "href": f"/d/unknown-{index}.html",
                    "title": "更正公告",
                    "project_no": "",
                    "published": "2026-10-02T09:00:00+08:00",
                }
            )
        self._listing("list.html", items)
        self._detail(
            "/d/related.html",
            project_no="GSB-2026-009",
            title="[GSB-2026-009] 相关项目招标公告",
            kind_label="招标公告",
            published="2026-10-01 09:00",
            content="投标截止时间：2026年12月1日 17:00。",
        )
        self._detail(
            "/d/unknown-0.html",
            project_no="GSB-2026-009",
            title="[GSB-2026-009] 相关项目取消公告",
            kind_label="取消公告",
            published="2026-10-02 09:00",
            content="本项目取消采购。",
        )

        result = self._scraper("GSB-2026-009", "/list.html", max_details=1).run()
        self.assertEqual(result.status, STATUS_PENDING)
        self.assertTrue(
            any("详情上限" in issue and "未确认" in issue for issue in result.issues),
            result.issues,
        )
        self.assertLessEqual(result.detail_count, 1)

    def test_cross_page_duplicate_keeps_catalog_info_and_fetches_once(self):
        first = [
            {
                "href": "/d/shared.html",
                "title": "取消公告",
                "project_no": "",
                "published": "2026-10-10T10:00:00+08:00",
            }
        ]
        second = [
            {
                "href": "/d/shared.html",
                "title": "取消公告",
                "project_no": "",
                "published": "",
            }
        ]
        self._listing(
            "p1.html",
            first,
            pagination='<a class="page-link next" rel="next" href="p2.html">下一页</a>',
        )
        self._listing("p2.html", second)
        self._detail(
            "/d/shared.html",
            project_no="GSB-2026-010",
            title="[GSB-2026-010] 某项目取消公告",
            kind_label="取消公告",
            published="2026-10-10 10:00",
            content="本项目取消采购。",
        )

        result = self._scraper("GSB-2026-010", "/p1.html").run()
        self.assertEqual(result.status, STATUS_CANCELLED)
        self.assertEqual(result.duplicates_skipped, 1)
        cancel = result.cancellation_notice
        # The second listing occurrence has no time; the merged row keeps the
        # publish time contributed by the first listing page.
        self.assertEqual(cancel.publish_time.strftime("%Y-%m-%d %H:%M"), "2026-10-10 10:00")

    def test_unnumbered_row_without_any_project_identifier_is_unconfirmed(self):
        self._listing(
            "list.html",
            [
                {
                    "href": "/d/mystery.html",
                    "title": "更正公告",
                    "project_no": "",
                    "published": "2026-10-03T09:00:00+08:00",
                }
            ],
        )
        # Detail page deliberately omits any project number.
        self.pages[f"{self.BASE}/d/mystery.html"] = DETAIL_TEMPLATE.format(
            data_attr="",
            title="更正公告",
            project_no="",
            kind_label="更正公告",
            published="2026-10-03 09:00",
            content="投标截止时间延期至2026年12月2日17:00。",
        )

        scraper = self._scraper("GSB-2026-011", "/list.html")
        result = scraper.run()
        self.assertEqual(result.status, STATUS_PENDING)
        self.assertTrue(any("无法确认是否归属" in issue for issue in result.issues))

    def test_detail_parse_outcome_marks_other_project(self):
        self._listing(
            "list.html",
            [
                {
                    "href": "/d/other.html",
                    "title": "招标公告",
                    "project_no": "",
                    "published": "2026-10-04T09:00:00+08:00",
                }
            ],
        )
        self._detail(
            "/d/other.html",
            project_no="OTHER-2026-200",
            title="[OTHER-2026-200] 其他项目招标公告",
            kind_label="招标公告",
            published="2026-10-04 09:00",
            content="投标截止时间：2026年11月9日 17:00。",
        )

        scraper = self._scraper("GSB-2026-012", "/list.html")
        result = scraper.run()
        # Confidently excluded: no pending issue about this record, no timeline.
        self.assertFalse(any("OTHER-2026-200" in issue for issue in result.issues))
        self.assertEqual(result.announcement_count, 0)

    def test_related_links_over_cap_are_hard_limited(self):
        items = []
        for index in range(3):
            items.append(
                {
                    "href": f"/d/related-{index}.html",
                    "title": f"[GSB-2026-014] 相关项目公告{index}",
                    "project_no": "GSB-2026-014",
                    "published": f"2026-10-0{index + 1}T09:00:00+08:00",
                }
            )
        self._listing("list.html", items)
        for index in range(3):
            self._detail(
                f"/d/related-{index}.html",
                project_no="GSB-2026-014",
                title=f"[GSB-2026-014] 相关项目公告{index}",
                kind_label="招标公告",
                published=f"2026-10-0{index + 1} 09:00",
                content=f"投标截止时间：2026年12月{index + 1}日 17:00。",
            )

        scraper = self._scraper("GSB-2026-014", "/list.html", max_details=2)
        result = scraper.run()
        self.assertEqual(result.status, STATUS_PENDING)
        self.assertEqual(result.detail_count, 2)
        self.assertTrue(
            any("相关详情链接超过上限 2" in issue for issue in result.issues)
        )

    def test_candidate_merge_promotes_related_flag_and_list_time(self):
        first = Candidate(
            url=f"{self.BASE}/d/x.html",
            title="招标公告",
            context="招标公告",
            found_on=f"{self.BASE}/p1.html",
            list_time=None,
            related=False,
        )
        second = Candidate(
            url=f"{self.BASE}/d/x.html",
            title="招标公告",
            context="项目编号：GSB-2026-013 招标公告",
            found_on=f"{self.BASE}/p2.html",
            list_time=parse_time("2026-10-05T09:00:00+08:00"),
            related=True,
        )
        first.merge_listing_info(second)
        self.assertTrue(first.related)
        self.assertEqual(first.list_time, parse_time("2026-10-05T09:00:00+08:00"))
        self.assertIn("GSB-2026-013", first.context)


def parse_time(raw):
    from tendercheck.parsing import parse_datetime

    return parse_datetime(raw)


if __name__ == "__main__":
    unittest.main()
