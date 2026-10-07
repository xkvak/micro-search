import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from search_engine import SearchEngine, title_and_body

BASE = Path(__file__).parent
INDEX = BASE.parent / "data/inverted"
LLM = "http://localhost:1234/v1/chat/completions"
MODEL = "gemma-4-26b-a4b-it-qat-mlx"
AI_ANSWER = os.environ.get("AI_ANSWER", "1") != "0"
PROMPT = """아래는 검색된 글의 제목과, 질문과 관련된 본문 일부·댓글입니다. 이것만 근거로 한국어 평문 3~5문장으로 답하세요.
- 문장마다 근거 글 번호를 [번호]로 붙이세요.
- 커뮤니티 글·댓글은 개인 의견입니다. 단정하지 말고, 의견이 갈리면 관점별로 묶어 "~라는 의견이 많고, ~라는 반론도 있다"처럼 전하세요.
- 답이 없으면 "검색 결과에서 답을 찾지 못했습니다."라고만 답하세요.

질문: {q}

검색 결과:
{docs}"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not INDEX.exists():
        raise RuntimeError(
            f"{INDEX} 없음. 먼저 `uv run python search_engine.py --index`로 색인을 만드세요."
        )
    app.state.engine = SearchEngine(INDEX)
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.globals["ai_answer"] = AI_ANSWER


@app.get("/")
def index(request: Request):
    engine = request.app.state.engine
    return templates.TemplateResponse(
        request, "index.html", {"num_docs": engine.num_docs}
    )


@app.get("/search")
def search(request: Request, q: str):
    engine = request.app.state.engine
    results = [
        post | {"score": s}
        for d, s in engine.search(q)
        if (post := load_post(engine.paths[d - 1]))
    ]
    return templates.TemplateResponse(
        request, "results.html", {"q": q, "results": results}
    )


@app.get("/answer")
def answer(request: Request, q: str):
    engine = request.app.state.engine
    posts = [p for d, _ in engine.search(q) if (p := load_post(engine.paths[d - 1]))]
    groups = {}
    for i, s in engine.snippets(q, [p["md"] for p in posts]):
        groups.setdefault(i, []).append(s)
    if not groups:
        return PlainTextResponse("검색 결과에서 답을 찾지 못했습니다.")
    docs = "\n\n".join(
        f"[{i + 1}] {posts[i]['title']}\n" + "\n".join(f"- {s}" for s in ss)
        for i, ss in groups.items()
    )
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT.format(q=q, docs=docs)}],
        "reasoning_effort": "none",
        "stream": True,
    }

    async def stream():
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", LLM, json=body) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        if line.startswith("data: {"):
                            for c in json.loads(line[6:])["choices"]:
                                yield c["delta"].get("content") or ""
        except httpx.HTTPError:
            yield "AI 요약을 불러오지 못했습니다. (LM Studio 서버와 모델 로드 확인)"

    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")


def load_post(path: str) -> dict | None:
    try:
        md = (BASE.parent / path).read_text()
    except FileNotFoundError:
        return None
    head = md.split("\n---\n", 1)[0].removeprefix("---\n")
    post = {k: json.loads(v) for k, v in (l.split(": ", 1) for l in head.splitlines())}
    body = title_and_body(md).strip().removeprefix(f"# {post['title']}")
    post["excerpt"] = " ".join(body.split())[:200]
    post["md"] = md
    return post
