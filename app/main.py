import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from search_engine import SearchEngine, title_and_body

BASE = Path(__file__).parent
INDEX = BASE.parent / "data/inverted"


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


def load_post(path: str) -> dict | None:
    try:
        md = (BASE.parent / path).read_text()
    except FileNotFoundError:
        return None
    head = md.split("\n---\n", 1)[0].removeprefix("---\n")
    post = {k: json.loads(v) for k, v in (l.split(": ", 1) for l in head.splitlines())}
    body = title_and_body(md).strip().removeprefix(f"# {post['title']}")
    post["excerpt"] = " ".join(body.split())[:200]
    return post
