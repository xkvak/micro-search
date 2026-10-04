"""클리앙. robots.txt(User-agent: *, 2026-09-22 확인) 기준으로 수집한다.

허용: /service/board/ — 단 /service/board/sold/(장터), /service/board/hongbo/(홍보)는 차단.
Disallow: /*?* 라 쿼리가 붙은 주소(목록 2페이지 이후 ?po=, 글 링크의 ?od=...)는 요청할 수 없다.
그래서 게시판마다 목록 첫 페이지(최신 글)만 보고, 글은 쿼리 없는 /service/board/{board}/{번호}로 요청한다.
매 실행마다 첫 페이지를 다시 확인해 새 글만 받는다(latest_only).
"""

import html, re, urllib.request

from crawler import Site, num

BASE_URL = "https://www.clien.net"
EXCLUDED = {"sold", "hongbo"}


class Clien(Site):
    name = "clien"
    domain = "clien.net"
    latest_only = True
    delay = (
        5.0,
        10.0,
    )
    body_selector = ".post_article"
    list_schema = {
        "name": "posts",
        "baseSelector": "div.list_item.symph_row[data-board-sn]",
        "fields": [{"name": "no", "type": "attribute", "attribute": "data-board-sn"}],
    }
    view_schema = {
        "name": "post",
        "baseSelector": "body",
        "fields": [
            {"name": "title", "selector": ".post_subject span", "type": "text"},
            {
                "name": "author",
                "selector": ".content_view .post_info .nickname",
                "type": "text",
                "default": "",
            },
            {
                "name": "date",
                "selector": ".post_author .view_count.date",
                "type": "text",
                "default": "",
            },
            {
                "name": "views",
                "selector": ".post_author .view_count strong",
                "type": "text",
                "default": "",
            },
            {
                "name": "upvotes",
                "selector": ".post_symph",
                "type": "text",
                "default": "",
            },
            {
                "name": "comments",
                "selector": ".comment_row[data-comment-sn]",
                "type": "nested_list",
                "fields": [
                    {"name": "no", "type": "attribute", "attribute": "data-comment-sn"},
                    {
                        "name": "cls",
                        "type": "attribute",
                        "attribute": "class",
                        "default": "",
                    },
                    {
                        "name": "author",
                        "selector": ".nickname span",
                        "type": "text",
                        "default": "",
                    },
                    {
                        "name": "date",
                        "selector": ".comment_time .timestamp",
                        "type": "text",
                        "default": "",
                    },
                    {
                        "name": "text",
                        "selector": ".comment_view",
                        "type": "html",
                        "default": "",
                    },
                ],
            },
        ],
    }

    def boards(self):
        """게시판 메뉴의 /service/board/<id> 링크 (robots.txt 차단 게시판 제외). 요청 1건."""
        req = urllib.request.Request(
            f"{BASE_URL}/service/board/park", headers={"User-Agent": "Mozilla/5.0"}
        )
        page = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        return sorted(
            set(re.findall(r'href="/service/board/([a-z_]+)"', page)) - EXCLUDED
        )

    def list_url(self, board, page):
        return f"{BASE_URL}/service/board/{board}"

    def view_url(self, board, no):
        return f"{BASE_URL}/service/board/{board}/{no}"

    def post_nos(self, board, rows):
        return {int(r["no"]) for r in rows}

    def last_page(self, board, page_html):
        return 0

    def parse_post(self, row, js_result, now):
        comments, root = [], None
        for c in row.get("comments") or []:
            is_reply = "re" in c["cls"]
            root = root if is_reply else c["no"]
            comments.append(
                {
                    "no": c["no"],
                    "parent": root if is_reply else None,
                    "author": c["author"],
                    "date": c["date"][:16],
                    "text": html.unescape(re.sub(r"<[^>]+>", " ", c["text"])),
                }
            )
        meta = {
            "title": row["title"],
            "author_nick": row["author"],
            "created_at": (
                row["date"].replace(" ", "T") + "+09:00" if row["date"] else ""
            ),
            "views": num(row["views"]),
            "upvotes": num(row["upvotes"]),
            "comment_count": len(comments),
        }
        return meta, comments


SITE = Clien()
