"""더쿠. robots.txt가 없다(/robots.txt → 404, 2026-09-22 확인)라 경로 제한은 없다.

HOT 게시판만 수집한다. HOT은 여러 게시판의 인기글 모음이라, 다른 게시판을 추가하면
같은 글이 게시판별로 중복 저장될 수 있다.
비회원은 작성 1시간 이내 댓글을 볼 수 없다(로그인은 하지 않음). 그 댓글은 빼고 저장하며,
빠진 개수를 hidden_comments로 남긴다.
"""

import html, re
from datetime import datetime

from crawler import Site, num

BASE_URL = "https://theqoo.net"
BOARDS = ["hot"]
HIDDEN = "commentWarningMessage"

COMMENTS_JS = r"""
const no = location.pathname.split('/').pop();
const load = async (cpage) => {
  const res = await fetch('/index.php', {
    method: 'POST',
    headers: {'X-Requested-With': 'XMLHttpRequest'},
    body: new URLSearchParams({act: 'dispTheqooContentCommentListTheqoo', document_srl: no, cpage}),
  });
  const text = await res.text();
  return text.trim() ? JSON.parse(text) : null;
};
const first = await load(0);
if (!first) return {blocked: true};
const out = [...(first.comment_list || [])];
for (let p = first.now_comment_page - 1; p >= 1; p--) {
  const d = await load(p);
  if (!d) return {blocked: true};
  out.push(...(d.comment_list || []));
}
return out;
"""


class TheQoo(Site):
    name = "theqoo"
    domain = "theqoo.net"
    body_selector = ".rd_body .xe_content"
    view_js = COMMENTS_JS
    list_schema = {
        "name": "posts",
        "baseSelector": "table tbody tr:not(.notice) td.title > a:first-child",
        "fields": [{"name": "href", "type": "attribute", "attribute": "href"}],
    }
    view_schema = {
        "name": "post",
        "baseSelector": "body",
        "fields": [
            {
                "name": "title",
                "selector": ".theqoo_document_header .title",
                "type": "text",
            },
            {
                "name": "date",
                "selector": ".btm_area .side.fr",
                "type": "text",
                "default": "",
            },
            {
                "name": "counts",
                "selector": ".count_container",
                "type": "html",
                "default": "",
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
        pattern = rf"/{board}/(\d+)(?:\?.*)?"
        return {
            int(m.group(1))
            for r in rows
            if (m := re.fullmatch(pattern, r.get("href", "")))
        }

    def last_page(self, board, page_html):
        return max(map(int, re.findall(rf"/{board}\?page=(\d+)", page_html)), default=0)

    def parse_post(self, row, js_result, now):
        raw = [c for c in js_result or [] if HIDDEN not in str(c.get("ct"))]
        hidden = len(js_result or []) - len(raw)
        comments = [
            {
                "no": str(c["srl"]),
                "parent": None,
                "author": "무명의 더쿠",
                "date": f"{c['rd'][:4]}-{c['rd'][4:6]}-{c['rd'][6:8]} {c['rd'][8:10]}:{c['rd'][10:12]}",
                "text": html.unescape(re.sub(r"<[^>]+>", " ", str(c["ct"]))),
            }
            for c in raw
        ]

        def count(icon):
            m = re.search(rf"{icon}[^>]*></i>\s*([\d,]+)", row["counts"])
            return num(m.group(1)) if m else 0

        try:
            created = datetime.strptime(row["date"].strip(), "%Y.%m.%d %H:%M").strftime(
                "%Y-%m-%dT%H:%M:00+09:00"
            )
        except ValueError:
            created = row["date"]
        meta = {
            "title": row["title"],
            "created_at": created,
            "views": count("fa-eye"),
            "comment_count": count("fa-comment-dots"),
            "hidden_comments": hidden,
        }
        return meta, comments


SITE = TheQoo()
