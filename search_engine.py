from array import array
from collections import Counter, defaultdict
import heapq
import math
from pathlib import Path
import pickle
from typing import Iterable, Iterator

from kiwipiepy import Kiwi


class SearchEngine:
    KIWI = Kiwi(num_workers=-1)
    KEEP = {"NNG", "NNP", "NR", "VV", "VA", "XR", "SL", "SN", "SH"}

    def __init__(self, k1=1.5, b=0.75) -> None:
        self.vocab: dict[str, list] = {}
        self.num_docs = 0
        self.doc_len = array("H", [0])
        self.avg_doc_len = 0

        self.k1 = k1
        self.b = b

    def index(self, docs: Iterable[str]):
        last = {}
        self.vocab = {}
        self.doc_len = array("H", [0])
        for docs_id, tokens in enumerate(self.tokenize(docs), 1):
            self.doc_len.append(len(tokens))
            for word, f_dt in Counter(tokens).items():
                if word not in self.vocab:
                    self.vocab[word] = [0, bytearray()]
                diff = [docs_id - last.get(word, 0), f_dt]
                self.vocab[word][0] += 1
                self.vocab[word][1] += self.vbyte_encode(diff)
                last[word] = docs_id
        self.num_docs = len(self.doc_len) - 1
        self.avg_doc_len = sum(self.doc_len) / max(self.num_docs, 1)

    def tokenize(self, docs: Iterable[str]) -> Iterator[list[str]]:
        for tokens in self.KIWI.tokenize(docs):
            yield [t.form.lower() for t in tokens if t.tag.split("-")[0] in self.KEEP]

    def vbyte_encode(self, nums) -> bytes:
        out = bytearray()
        for x in nums:
            x -= 1
            while x >= 128:
                out.append(128 + x % 128)
                x = x // 128 - 1
            out.append(x)

        return bytes(out)

    def vbyte_decode(self, data) -> Iterator[int]:
        x, p = 0, 1
        for b in data:
            if b >= 128:
                x += (b - 127) * p
                p *= 128
            else:
                yield x + (b + 1) * p
                x, p = 0, 1

    def idf(self, t: str) -> float:
        f_t = self.vocab[t][0]
        return math.log((self.num_docs - f_t + 0.5) / (f_t + 0.5) + 1)

    def bm25(self, kw: str) -> dict[int, float]:
        result = {}
        idf_score = self.idf(kw)
        decoded = self.vbyte_decode(self.vocab[kw][1])
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
        for t in next(self.tokenize([query])):
            if t in self.vocab:
                for d, s in self.bm25(t).items():
                    scores[d] += s

        return heapq.nlargest(r, scores.items(), key=lambda x: x[1])


def title_and_body(md: str) -> str:
    """저장된 글에서 맨 앞 메타데이터(--- ... ---)와 댓글(## 댓글 이후)을 뺀 제목 + 본문."""
    return md.split("\n---\n", 1)[1].rsplit("\n## 댓글\n", 1)[0]


def build_index(paths: list[Path], text=title_and_body):
    engine = SearchEngine()
    engine.index(text(p.read_text()) for p in paths)
    engine.paths = [str(p) for p in paths]
    tmp = Path("data/index.pkl.tmp")
    tmp.write_bytes(pickle.dumps(engine))
    tmp.replace("data/index.pkl")  # 서버가 쓰다 만 파일을 읽지 않도록 한 번에 교체
    print(f"{engine.num_docs}개 문서 색인 → data/index.pkl")


if __name__ == "__main__":
    # __main__.SearchEngine으로 pickle되면 서버에서 못 읽으므로 모듈로 다시 import해 실행
    import search_engine

    search_engine.build_index(sorted(Path("data").glob("*/posts/*/*.md")))
