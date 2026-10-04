"""FM코리아. robots.txt가 일반 크롤러(User-agent: *)에 허용하는 게시판만 수집한다.

robots.txt(확인일 2026-09-22): 기본 전부 차단(Disallow: /), 허용은 /, /best, /best2, /humor.
listStyle=, search_keyword=, module_srl=, m=0/1/6, act=IS 같은 쿼리도 차단한다.
사이트가 쓰는 /index.php?mid=... 링크와 루트 글 주소 /번호는 허용 경로 밖이라,
목록은 /{board}?page=N, 글은 /{board}/번호 로 요청한다 (두 형식 모두 정상 응답 확인).
"""

import html, re
from datetime import datetime, timedelta

from crawler import Site, num

BASE_URL = "https://www.fmkorea.com"
BOARDS = ["best", "best2", "humor"]
UNITS = {"초": "seconds", "분": "minutes", "시간": "hours", "일": "days"}


def fm_date(s, now):
    """'2026.09.22 19:24' 또는 'N 분/시간/일 전' → datetime. 모르는 형식이면 None."""
    s = s.strip()
    if m := re.fullmatch(r"(\d+)\s*(초|분|시간|일) 전", s):
        return now - timedelta(**{UNITS[m.group(2)]: int(m.group(1))})
    try:
        return datetime.strptime(s, "%Y.%m.%d %H:%M")
    except ValueError:
        return None


class FMKorea(Site):
    name = "fmkorea"
    domain = "fmkorea.com"
    delay = (
        2.0,
        4.0,
    )
    body_selector = "article .xe_content"
    list_schema = {
        "name": "posts",
        "baseSelector": "li h3.title a, table.bd_lst tbody tr:not(.notice) td.title > a",
        "fields": [{"name": "href", "type": "attribute", "attribute": "href"}],
    }
    view_schema = {
        "name": "post",
        "baseSelector": "body",
        "fields": [
            {"name": "title", "selector": ".rd_hd .np_18px", "type": "text"},
            {
                "name": "author",
                "selector": ".rd_hd .member_plate",
                "type": "text",
                "default": "",
            },
            {"name": "date", "selector": ".rd_hd .date", "type": "text", "default": ""},
            {
                "name": "stats",
                "selector": ".rd_hd .btm_area .side.fr",
                "type": "text",
                "default": "",
            },
            {
                "name": "comments",
                "selector": '.fdb_lst_ul > li.fdb_itm:not([id$="_"])',
                "type": "nested_list",
                "fields": [
                    {"name": "id", "type": "attribute", "attribute": "id"},
                    {
                        "name": "author",
                        "selector": ".meta .member_plate",
                        "type": "text",
                        "default": "",
                    },
                    {
                        "name": "date",
                        "selector": ".meta .date",
                        "type": "text",
                        "default": "",
                    },
                    {
                        "name": "text",
                        "selector": ".comment-content .xe_content",
                        "type": "html",
                        "default": "",
                    },
                    {
                        "name": "parent",
                        "selector": ".findParent",
                        "type": "attribute",
                        "attribute": "href",
                        "default": "",
                    },
                ],
            },
        ],
    }

    def boards(self):
        return BOARDS

    def list_url(self, board, page):
        return f"{BASE_URL}/{board}?page={page}"

    def view_url(self, board, no):
        return f"{BASE_URL}/{board}/{no}"

    def post_nos(self, board, rows):
        pattern = rf"/(?:{board}/)?(\d+)(?:#comment)?"
        return {
            int(m.group(1))
            for r in rows
            if (m := re.fullmatch(pattern, r.get("href", "")))
        }

    def last_page(self, board, page_html):
        return max(
            map(int, re.findall(rf"mid={board}&(?:amp;)?page=(\d+)", page_html)),
            default=0,
        )

    def parse_post(self, row, js_result, now):
        def stat(label):
            m = re.search(rf"{label}\s*([\d,]+)", row["stats"])
            return num(m.group(1)) if m else 0

        comments = []
        for c in row.get("comments") or []:
            parent = re.search(r"#comment_(\d+)", c["parent"])
            dt = fm_date(c["date"], now)
            comments.append(
                {
                    "no": c["id"].removeprefix("comment_"),
                    "parent": parent.group(1) if parent else None,
                    "author": c["author"],
                    "date": f"{dt:%Y-%m-%d %H:%M}" if dt else c["date"],
                    "text": html.unescape(re.sub(r"<[^>]+>", " ", c["text"])),
                }
            )
        dt = fm_date(row["date"], now)
        meta = {
            "title": row["title"],
            "author_nick": row["author"],
            "created_at": f"{dt:%Y-%m-%dT%H:%M}:00+09:00" if dt else row["date"],
            "views": stat("조회 수"),
            "upvotes": stat("추천 수"),
            "comment_count": stat("댓글"),
        }
        return meta, comments


SITE = FMKorea()
