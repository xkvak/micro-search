import json
import pickle
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from search_engine import title_and_body

BASE = Path(__file__).parent
INDEX = BASE.parent / "data/index.pkl"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not INDEX.exists():
        raise RuntimeError(
            f"{INDEX} 없음. 먼저 `uv run python search_engine.py`로 색인을 만드세요."
        )
    app.state.engine = pickle.loads(INDEX.read_bytes())
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


@app.get("/")
async def index(request: Request):
    engine = request.app.state.engine
    return templates.TemplateResponse(
        request, "index.html", {"num_docs": engine.num_docs}
    )


@app.get("/search")
async def search(request: Request, q: str):
    engine = request.app.state.engine
    results = [
        load_post(engine.paths[d - 1]) | {"score": s} for d, s in engine.search(q)
    ]
    return templates.TemplateResponse(
        request, "results.html", {"q": q, "results": results}
    )


def load_post(path: str) -> dict:
    """저장된 글 → frontmatter 값 + 본문 앞부분(excerpt)."""
    md = (BASE.parent / path).read_text()
    head = md.split("\n---\n", 1)[0].removeprefix("---\n")
    post = {k: json.loads(v) for k, v in (l.split(": ", 1) for l in head.splitlines())}
    body = title_and_body(md).strip().removeprefix(f"# {post['title']}")
    post["excerpt"] = " ".join(body.split())[:200]
    return post
