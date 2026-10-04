"""디시인사이드 일반 갤러리. 갤러리 목록은 DC가 제공하는 사이트맵에서 받는다."""

import html, re, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

from crawler import Site, num

BASE_URL = "https://gall.dcinside.com"
SITEMAP_URL = "https://www.dcinside.com/_seo/main_gall.xml"

BOARDS = []
LIST_PAGES = 1

COMMENTS_JS = r"""
const count = parseInt((document.querySelector('.gallview_head .gall_comment')?.textContent || '').replace(/\D/g, '')) || 0;
const esno = document.querySelector('#e_s_n_o')?.value;
if (!count || !esno) return [];
const q = new URL(location.href).searchParams, id = q.get('id'), no = q.get('no');
const out = [];
let seen = 0;
for (let page = 1; ; page++) {
  const res = await fetch('/board/comment/', {
    method: 'POST',
    headers: {'X-Requested-With': 'XMLHttpRequest'},
    body: new URLSearchParams({id, no, cmt_id: id, cmt_no: no, e_s_n_o: esno, comment_page: page, sort: '', _GALLTYPE_: 'G'}),
  });
  const text = await res.text();
  if (!text.trim()) return {blocked: true};
  const d = JSON.parse(text), c = d.comments || [];
  seen += c.length;
  out.push(...c);
  if (!c.length || seen >= +d.total_cnt) return out;
}
"""


def normalize_cmt_date(s, now):
    """댓글 시각 'MM.DD HH:MM:SS'(올해) 또는 'YYYY.MM.DD HH:MM:SS' → 'YYYY-MM-DD HH:MM'"""
    if not s:
        return ""
    if s.count(".") == 1:
        dt = datetime.strptime(f"{now.year}.{s}", "%Y.%m.%d %H:%M:%S")
        if dt > now:
            dt = dt.replace(year=now.year - 1)
    else:
        dt = datetime.strptime(s, "%Y.%m.%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M")


def clean_memo(memo):
    memo = re.sub(r"<img[^>]*written_dccon[^>]*>", "(디시콘)", memo or "")
    memo = re.sub(r"<br\s*/?>", " ", memo)
    return html.unescape(re.sub(r"<[^>]+>", "", memo)).strip()


def _field(name, selector, attribute=None):
    """view_schema 필드 하나 (없으면 빈 문자열)."""
    if attribute:
        return {
            "name": name,
            "selector": selector,
            "type": "attribute",
            "attribute": attribute,
            "default": "",
        }
    return {"name": name, "selector": selector, "type": "text", "default": ""}


class DCInside(Site):
    name = "dcinside"
    domain = "dcinside.com"
    body_selector = ".write_div"
    view_js = COMMENTS_JS
    max_pages = LIST_PAGES
    list_schema = {
        "name": "posts",
        "baseSelector": "tr.ub-content[data-no]",
        "fields": [
            {"name": "no", "type": "attribute", "attribute": "data-no"},
            {"name": "type", "type": "attribute", "attribute": "data-type"},
        ],
    }
    view_schema = {
        "name": "post",
        "baseSelector": "body",
        "fields": [
            {
                "name": "title",
                "selector": ".gallview_head .title_subject",
                "type": "text",
            },
            _field("head", ".gallview_head .title_headtext"),
            _field("author_nick", ".gallview_head .gall_writer", "data-nick"),
            _field("author_id", ".gallview_head .gall_writer", "data-uid"),
            _field("author_ip", ".gallview_head .gall_writer", "data-ip"),
            _field("created_at", ".gallview_head .gall_date", "title"),
            _field("views", ".gallview_head .gall_count"),
            _field("upvotes", "p.up_num"),
            _field("downvotes", "p.down_num"),
            _field("comment_count", ".gallview_head .gall_comment"),
        ],
    }

    def boards(self):
        """BOARDS에 적은 갤러리, 비어 있으면 사이트맵의 일반 갤러리 전체. 요청 1건 (268KB)."""
        if BOARDS:
            return BOARDS
        root = ET.fromstring(urllib.request.urlopen(SITEMAP_URL, timeout=30).read())
        locs = root.iterfind(
            "s:url/s:loc", {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        )
        return [
            m.group(1)
            for loc in locs
            if (m := re.search(r"/board/lists/\?id=([^&]+)", loc.text))
        ]

    def list_url(self, board, page):
        return f"{BASE_URL}/board/lists/?id={board}&exception_mode=recommend&list_num=100&page={page}"

    def view_url(self, board, no):
        return f"{BASE_URL}/board/view/?id={board}&no={no}"

    def post_nos(self, board, rows):
        return {int(r["no"]) for r in rows if r.get("type") != "icon_notice"}

    def last_page(self, board, page_html):
        pages = re.findall(
            rf"lists/\?id={re.escape(board)}&(?:amp;)?page=(\d+)", page_html
        )
        return max(map(int, pages), default=0)

    def parse_post(self, row, js_result, now):
        comments = [
            {
                "no": c["no"],
                "parent": c["c_no"] if int(c["depth"]) else None,
                "author": c["name"] + (f"({c['ip']})" if c.get("ip") else ""),
                "date": normalize_cmt_date(c["reg_date"], now),
                "text": clean_memo(c["memo"]),
            }
            for c in js_result or []
            if c.get("nicktype") != "COMMENT_BOY" and c.get("del_yn") != "Y"
        ]
        meta = {
            "title": row["title"],
            "head": row["head"].strip("[]"),
            "author_nick": row["author_nick"],
            "author_id": row["author_id"],
            "author_ip": row["author_ip"],
            "created_at": (
                row["created_at"].replace(" ", "T") + "+09:00"
                if row["created_at"]
                else ""
            ),
            "views": num(row["views"]),
            "upvotes": num(row["upvotes"]),
            "downvotes": num(row["downvotes"]),
            "comment_count": num(row["comment_count"]),
        }
        return meta, comments


SITE = DCInside()
