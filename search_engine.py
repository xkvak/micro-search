from array import array
from collections import Counter, defaultdict
import heapq
from itertools import batched, groupby
import math
from operator import itemgetter
import os
from pathlib import Path
import pickle
import re
import shutil
import sqlite3
import sys
import threading
from typing import Iterable, Iterator

from kiwipiepy import Kiwi

KIWI = Kiwi(num_workers=-1)
KEEP = {"NNG", "NNP", "NR", "VV", "VA", "XR", "SL", "SN", "SH"}


def tokenize(docs: Iterable[str]) -> Iterator[list[str]]:
    for tokens in KIWI.tokenize(docs):
        yield [t.form.lower() for t in tokens if t.tag.split("-")[0] in KEEP]


def vbyte_encode(nums) -> bytes:
    out = bytearray()
    for x in nums:
        x -= 1
        while x >= 128:
            out.append(128 + x % 128)
            x = x // 128 - 1
        out.append(x)

    return bytes(out)


def vbyte_decode(data) -> Iterator[int]:
    x, p = 0, 1
    for b in data:
        if b >= 128:
            x += (b - 127) * p
            p *= 128
        else:
            yield x + (b + 1) * p
            x, p = 0, 1


def invert(docs: Iterable[str], start=0) -> tuple[dict, dict, list[int]]:
    vocab, last, lens = {}, {}, []
    for doc_id, tokens in enumerate(tokenize(docs), start + 1):
        lens.append(len(tokens))
        for word, f_dt in Counter(tokens).items():
            if word not in vocab:
                vocab[word] = [0, bytearray()]
            vocab[word][0] += 1
            vocab[word][1] += vbyte_encode([doc_id - last.get(word, 0), f_dt])
            last[word] = doc_id
    return vocab, last, lens


class SearchEngine:
    def __init__(self, path: Path, k1=1.5, b=0.75) -> None:
        self.k1 = k1
        self.b = b
        self.db = sqlite3.connect(path / "vocab.db", check_same_thread=False)
        self.lock = threading.Lock()
        self.fd = os.open(path / "postings.bin", os.O_RDONLY)
        docs = self.db.execute("SELECT path, len FROM docs ORDER BY id").fetchall()
        self.paths = [p for p, _ in docs]
        self.doc_len = array("I", [0, *(n for _, n in docs)])
        self.num_docs = len(docs)
        self.avg_doc_len = sum(self.doc_len) / max(self.num_docs, 1)

    def postings(self, t: str):
        with self.lock:
            row = self.db.execute(
                "SELECT df, off, len FROM vocab WHERE term = ?", (t,)
            ).fetchone()
        return row and (row[0], os.pread(self.fd, row[2], row[1]))

    def idf(self, f_t: int) -> float:
        return math.log((self.num_docs - f_t + 0.5) / (f_t + 0.5) + 1)

    def bm25(self, f_t: int, data) -> dict[int, float]:
        result = {}
        idf_score = self.idf(f_t)
        decoded = vbyte_decode(data)
        doc_id = 0
        for gap in decoded:
            doc_id += gap
            freq = next(decoded)
            numerator = freq * (self.k1 + 1)
            denominator = freq + self.k1 * (
                1 - self.b + self.b * self.doc_len[doc_id] / self.avg_doc_len
            )
            result[doc_id] = idf_score * numerator / denominator

        return result

    def search(self, query: str, r=10) -> list[tuple[int, float]]:
        scores = defaultdict(float)
        for t in next(tokenize([query])):
            if p := self.postings(t):
                for d, s in self.bm25(*p).items():
                    scores[d] += s

        return heapq.nlargest(r, scores.items(), key=lambda x: x[1])


COMMENT_META = re.compile(r"^(\s*- \[c\d+[^\]]*\] .*|## 댓글)$", re.M)


def title_and_body(md: str) -> str:
    return md.split("\n---\n", 1)[1].rsplit("\n## 댓글\n", 1)[0]


def index_text(md: str) -> str:
    return COMMENT_META.sub("", md.split("\n---\n", 1)[1])


INDEX = Path("data/inverted")
RUN_DOCS = 5000


def build(paths: list[Path], out=INDEX, run_docs=RUN_DOCS):
    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    db = sqlite3.connect(tmp / "vocab.db")
    db.executescript("""
        CREATE TABLE vocab(term TEXT PRIMARY KEY, df INTEGER, off INTEGER, len INTEGER) WITHOUT ROWID;
        CREATE TABLE docs(id INTEGER PRIMARY KEY, path TEXT, len INTEGER);
        """)
    runs, n = [], 0
    for chunk in batched(paths, run_docs):
        vocab, last, lens = invert((index_text(p.read_text()) for p in chunk), start=n)
        db.executemany(
            "INSERT INTO docs VALUES (?, ?, ?)",
            zip(range(n + 1, n + len(chunk) + 1), map(str, chunk), lens),
        )
        n += len(chunk)
        runs.append(tmp / f"run{len(runs)}")
        with runs[-1].open("wb") as f:
            for t in sorted(vocab):
                pickle.dump((t, *vocab[t], last[t]), f)
        print(f"런 {len(runs)}: 글 {n:,}/{len(paths):,}", flush=True)

    with (tmp / "postings.bin").open("wb") as f:
        db.executemany("INSERT INTO vocab VALUES (?, ?, ?, ?)", merge_runs(runs, f))
    db.commit()
    db.close()
    for r in runs:
        r.unlink()

    old = out.with_name(out.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if out.exists():
        out.rename(old)
    tmp.rename(out)
    shutil.rmtree(old, ignore_errors=True)
    print(f"{n:,}개 문서 색인 → {out}")


def read_run(path: Path):
    with path.open("rb") as f:
        while True:
            try:
                yield pickle.load(f)
            except EOFError:
                return


def merge_runs(runs: list[Path], out):
    merged = heapq.merge(*map(read_run, runs), key=itemgetter(0))
    for term, group in groupby(merged, key=itemgetter(0)):
        df, data, last = 0, bytearray(), 0
        for _, r_df, r_data, r_last in group:
            first = next(vbyte_decode(r_data))
            data += vbyte_encode([first - last]) + r_data[len(vbyte_encode([first])) :]
            df, last = df + r_df, r_last
        yield term, df, out.tell(), len(data)
        out.write(data)


if __name__ == "__main__":
    if "--index" not in sys.argv:
        sys.exit("사용법: uv run python search_engine.py --index")
    build(sorted(Path("data").glob("*/posts/*/*.md")))
