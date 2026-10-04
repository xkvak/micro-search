"""사이트 공통 크롤러. 사이트마다 다른 부분은 sites/<이름>.py의 Site 하위 클래스가 맡는다.

실행: python crawler.py        # sites/의 모든 사이트를 동시에, 끝없이 (Ctrl+C로 멈춤)
      python crawler.py dc     # 하나만 (sites/ 안의 파일 이름)
동작: 게시판을 한 바퀴 돌고 PASS_PAUSE 쉰 뒤 다시 돈다. 다 받은 게시판은 새 글만 확인하고,
      차단되면 기다렸다가 이어서, 그 밖의 오류는 기록하고 브라우저를 다시 띄워 계속한다.
새 사이트: sites/<이름>.py에 Site를 상속한 클래스를 만들고 SITE = 클래스() 로 내보낸다.
"""

import asyncio, importlib, json, re, sys, time
from collections import Counter
from contextlib import aclosing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    DefaultMarkdownGenerator,
    JsonCssExtractionStrategy,
    MemoryAdaptiveDispatcher,
    RateLimiter,
)

DATA_DIR = Path("data")
KST = timezone(timedelta(hours=9))
BLOCK_PAUSE = 900
LIST_CHUNK = 10
PASS_PAUSE = 600


class Blocked(Exception):
    """사이트가 요청을 막았다 (빈 본문, 4xx/5xx). wait: 사이트가 알려 준 대기 시간(초, Retry-After)"""

    def __init__(self, url, wait=None):
        super().__init__(url)
        self.wait = wait


class Site:
    """사이트마다 다른 부분. 하위 클래스가 아래 속성과 메서드를 채운다."""

    name = ""
    domain = ""
    check_robots = True
    concurrency = 2
    delay = (1.0, 2.0)
    list_schema: dict
    view_schema: dict
    body_selector = ""
    view_js = None
    latest_only = False
    max_pages = 0

    def boards(self):
        """인자가 없을 때 수집할 게시판 id 목록."""
        raise NotImplementedError

    def list_url(self, board, page):
        raise NotImplementedError

    def view_url(self, board, no):
        raise NotImplementedError

    def post_nos(self, board, rows):
        """목록 추출 rows → 글 번호 집합 (공지 제외)."""
        raise NotImplementedError

    def last_page(self, board, page_html):
        """목록 HTML에 보이는 가장 큰 페이지 번호 (없으면 0)."""
        raise NotImplementedError

    def parse_post(self, row, js_result, now):
        """view_schema 1행 + view_js 결과 → (meta, comments).

        meta에는 최소 "title"이 있어야 한다. comments: [{no, parent, author, date, text}],
        parent는 답글이면 부모 댓글 번호, 아니면 None. now는 naive KST.
        """
        raise NotImplementedError


def num(s):
    """'조회 1,234' → 1234"""
    return int(re.sub(r"\D", "", s or "") or 0)


def classify(r):
    """crawl4ai 결과 → 'ok' | 'deleted' | 'robots'. 차단이면 Blocked, 그 외는 RuntimeError."""
    err = r.error_message or ""
    if "robots.txt" in err:
        return "robots"
    if r.status_code == 404 or "HTTP 404" in err:
        return "deleted"
    if "net::ERR_" in err:
        raise Blocked(r.url)
    if (r.status_code or 0) >= 400 or (
        r.status_code == 200 and len(r.html or "") < 1000
    ):
        headers = {k.lower(): v for k, v in (r.response_headers or {}).items()}
        wait = headers.get("retry-after", "")
        raise Blocked(r.url, int(wait) if wait.isdigit() else None)
    if r.success:
        return "ok"
    raise RuntimeError(err[:300])


def js_result(r):
    """view_js 반환값. crawl4ai는 빈 리스트를 {"success": True}로, JS 예외를 {"success": False}로 바꾼다."""
    raw = (r.js_execution_result or {}).get("results", [None])[0]
    if isinstance(raw, dict):
        if raw.get("blocked"):
            raise Blocked(f"js {r.url}")
        if raw.get("success") is False:
            raise RuntimeError(f"JS 실패: {raw.get('error')}")
        if raw == {"success": True}:
            return []
    return raw


def to_md(meta, body, comments):
    """frontmatter + 본문 + 댓글. 답글(parent 있음)은 부모 댓글 아래에 들여쓴다."""
    fm = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items())
    parts = [f"---\n{fm}\n---", f"# {meta['title']}", body.strip()]
    if comments:
        lines = []
        for c in sorted(
            comments, key=lambda c: (int(c["parent"] or c["no"]), int(c["no"]))
        ):
            pad = "  " if c["parent"] else ""
            ref = f"c{c['no']} → c{c['parent']}" if c["parent"] else f"c{c['no']}"
            text = " ".join(str(c["text"]).split())
            lines += [f"{pad}- [{ref}] {c['author']} · {c['date']}", f"{pad}  {text}"]
        parts += ["## 댓글", "\n".join(lines)]
    return "\n\n".join(parts) + "\n"


class Crawler:
    """Site 하나를 멈추지 않고 수집한다: 목록 → 글 → MD.

    차단되면 기다렸다가 같은 게시판을 재시도하고, 다 받은 게시판은 다음 바퀴부터 새 글만 확인한다.
    중단했다 다시 실행해도 이어서 수집한다.
    """

    def __init__(self, site):
        self.site = site
        self.dir = DATA_DIR / site.name
        self.rate_limiter = RateLimiter(base_delay=site.delay)
        common = dict(
            check_robots_txt=site.check_robots,
            stream=True,
            verbose=False,
            wait_until="load",
            magic=True,
            page_timeout=30000,
            scan_full_page=True,
            scroll_delay=0.5,
        )

        self.list_config = CrawlerRunConfig(
            extraction_strategy=JsonCssExtractionStrategy(site.list_schema),
            **common,
        )
        self.view_config = CrawlerRunConfig(
            extraction_strategy=JsonCssExtractionStrategy(site.view_schema),
            js_code=site.view_js,
            target_elements=[site.body_selector],
            markdown_generator=DefaultMarkdownGenerator(
                options={"ignore_links": True, "ignore_images": True, "body_width": 0}
            ),
            exclude_all_images=True,
            exclude_external_links=True,
            exclude_social_media_links=True,
            **common,
        )

    def dispatcher(self):
        return MemoryAdaptiveDispatcher(
            max_session_permit=self.site.concurrency, rate_limiter=self.rate_limiter
        )

    def log_failure(self, what, err):
        with (self.dir / "failed.log").open("a", encoding="utf-8") as f:
            f.write(
                f"{datetime.now(KST):%Y-%m-%d %H:%M:%S}\t{what}\t{str(err)[:300]!r}\n"
            )

    async def fetch_post_nos(self, crawler, board, pages):
        """목록 페이지들 → (글 번호 집합, 보이는 가장 큰 페이지 번호). robots.txt 차단이면 None.

        글을 못 찾은 페이지는 그냥 빈 것으로 둔다. 차단은 classify가 Blocked로 올린다.
        """
        nos, last = set(), 0
        urls = [self.site.list_url(board, p) for p in pages]
        async with aclosing(
            await crawler.arun_many(
                urls, config=self.list_config, dispatcher=self.dispatcher()
            )
        ) as results:
            async for r in results:
                status = classify(r)
                if status == "robots":
                    return None
                if status == "deleted":
                    continue
                last = max(last, self.site.last_page(board, r.html))
                nos |= self.site.post_nos(
                    board, json.loads(r.extracted_content or "[]")
                )
        return nos, last

    async def save_post(self, r, board, no, folder):
        """글 결과 하나를 MD로 저장한다. 반환: 'ok' | 'deleted' | 'robots'"""
        status = classify(r)
        if status != "ok":
            return status
        rows = json.loads(r.extracted_content or "[]")
        if not rows or not rows[0].get("title"):
            raise RuntimeError("메타데이터 추출 실패")
        now = datetime.now(KST)
        meta, comments = self.site.parse_post(
            rows[0], js_result(r), now.replace(tzinfo=None)
        )
        meta = {
            "id": f"{self.site.name}_{board}_{no}",
            "site": self.site.name,
            "board": board,
            "post_no": no,
            "url": r.url,
            **meta,
            "crawled_at": now.isoformat(timespec="seconds"),
        }
        path = folder / f"{no}.md"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(to_md(meta, str(r.markdown or ""), comments), encoding="utf-8")
        tmp.replace(path)
        print(
            f"{now:%H:%M:%S} {self.site.name}/{board} {no} 댓글 {len(comments)} · {meta['title'][:40]}",
            flush=True,
        )
        return "ok"

    async def crawl_board(self, crawler, board, update=False):
        """게시판 하나를 목록 LIST_CHUNK페이지씩 수집한다. Blocked는 호출자에게 올린다.

        update=True(다 받은 게시판, latest_only 사이트)면 1페이지부터 한 페이지씩 새 글만 받고,
        새 글이 없는 페이지를 만나면 멈춘다. 재개 지점은 쓰지 않는다.
        """
        folder = self.dir / "posts" / board
        folder.mkdir(parents=True, exist_ok=True)
        mark = folder / ".next_page"
        start = int(mark.read_text()) if mark.exists() and not update else 1
        if self.site.max_pages and start > self.site.max_pages:
            return Counter()
        pages = [start]
        stats = Counter()
        while pages:
            got = await self.fetch_post_nos(crawler, board, pages)
            if got is None:
                return Counter(robots=1)
            nos, last = got
            if self.site.max_pages:
                last = min(last, self.site.max_pages)
            if not nos:
                return stats or Counter(empty=1)
            todo = {
                self.site.view_url(board, n): n
                for n in sorted(nos, reverse=True)
                if not (folder / f"{n}.md").exists()
            }
            if update and not todo:
                break
            sem = asyncio.Semaphore(self.site.concurrency)

            async def save_one(r):
                try:
                    stats[await self.save_post(r, board, todo[r.url], folder)] += 1
                except Blocked:
                    raise
                except Exception as e:
                    stats["failed"] += 1
                    self.log_failure(r.url, e)
                finally:
                    sem.release()

            try:
                async with (
                    asyncio.TaskGroup() as tg,
                    aclosing(
                        await crawler.arun_many(
                            list(todo),
                            config=self.view_config,
                            dispatcher=self.dispatcher(),
                        )
                    ) as results,
                ):
                    async for r in results:
                        await sem.acquire()
                        tg.create_task(save_one(r))
            except* Blocked as eg:
                raise eg.exceptions[0]
            nxt = pages[-1] + 1
            if not stats["failed"] and not update:
                mark.write_text(str(nxt))
            pages = list(range(nxt, min(nxt + (1 if update else LIST_CHUNK), last + 1)))
        return stats

    async def run(self):
        """끝없이 수집한다: 게시판을 한 바퀴 돌고 PASS_PAUSE만큼 쉰 뒤 다시 돈다.

        어떤 오류도 루프를 끝내지 않는다. 브라우저가 죽는 등 예상 못 한 오류가 나면
        기록하고 1분 뒤 브라우저부터 다시 띄운다. Ctrl+C(KeyboardInterrupt)로만 멈춘다.
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        restart = 60
        while True:
            try:
                browser = BrowserConfig(
                    headless=True,
                    verbose=False,
                    memory_saving_mode=True,
                    max_pages_before_recycle=500,
                )
                async with AsyncWebCrawler(config=browser) as crawler:
                    while True:
                        empty = await self.run_pass(crawler)
                        restart = 60
                        print(
                            f"{self.site.name} 한 바퀴 끝"
                            f"{f', 글 없는 게시판 {empty}개' if empty else ''}, "
                            f"{PASS_PAUSE // 60}분 뒤 새 글 확인",
                            flush=True,
                        )
                        await asyncio.sleep(PASS_PAUSE)
            except Exception as e:
                self.log_failure("run", e)
                print(
                    f"{self.site.name} 오류로 재시작 ({e!r}), {restart // 60}분 뒤",
                    flush=True,
                )
                await asyncio.sleep(restart)
                restart = min(restart * 2, 3600)

    async def run_pass(self, crawler):
        """게시판 한 바퀴. 다 받은 게시판(done)과 latest_only 사이트는 새 글만, 나머지는 이어서 끝까지."""
        boards = await asyncio.to_thread(self.site.boards)
        done_path = self.dir / "done.txt"
        done = set(done_path.read_text().split()) if done_path.exists() else set()
        strikes = 0
        empty = 0
        for i, board in enumerate(boards, 1):
            update = board in done or self.site.latest_only
            t0 = time.time()
            while True:
                try:
                    stats = await self.crawl_board(crawler, board, update)
                    strikes = 0
                    break
                except Blocked as e:
                    wait = min((e.wait or BLOCK_PAUSE) * 2**strikes, 3600)
                    strikes += 1
                    print(
                        f"{self.site.name} 차단 감지 ({e}), {wait // 60}분 대기",
                        flush=True,
                    )
                    await asyncio.sleep(wait)
                except Exception as e:
                    self.log_failure(board, e)
                    stats = Counter(failed=1)
                    break
            empty += stats["empty"]
            if not update and not stats["failed"] and not stats["empty"]:
                with done_path.open("a") as f:
                    f.write(board + "\n")
            if stats["ok"] or stats["failed"]:
                print(
                    f"[{i}/{len(boards)}] {self.site.name}/{board} {'새 글' if update else '저장'} {stats['ok']}, "
                    f"삭제 {stats['deleted']}, 실패 {stats['failed']}, {(time.time() - t0) / 60:.1f}분",
                    flush=True,
                )
        return empty


SITES_DIR = Path(__file__).parent / "sites"


async def main():
    names = sys.argv[1:] or sorted(p.stem for p in SITES_DIR.glob("*.py"))
    sites = [importlib.import_module(f"sites.{n}").SITE for n in names]
    await asyncio.gather(*(Crawler(s).run() for s in sites))


if __name__ == "__main__":
    import crawler

    asyncio.run(crawler.main())
