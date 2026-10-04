from array import array
from collections import Counter, defaultdict
import heapq
import math
from typing import Iterator

from kiwipiepy import Kiwi


class SearchEngine:
    KIWI = Kiwi(num_workers=-1)
    KEEP = {"NNG", "NNP", "NR", "VV", "VA", "XR", "SL", "SN", "SH"}

    def __init__(self, docs: list[str], k1=1.5, b=0.75) -> None:
        self.vocab: dict[str, list] = {}
        self.docs = docs
        self.num_docs = len(docs)
        self.doc_len = array("H", [0]) * (self.num_docs + 1)
        self.avg_doc_len = 0

        self.k1 = k1
        self.b = b

    def index(self):
        last = {}
        self.vocab = {}
        for docs_id, tokens in enumerate(self.tokenize(self.docs), 1):
            self.doc_len[docs_id] = len(tokens)
            for word, f_dt in Counter(tokens).items():
                if word not in self.vocab:
                    self.vocab[word] = [0, bytearray()]
                diff = [docs_id - last.get(word, 0), f_dt]
                self.vocab[word][0] += 1
                self.vocab[word][1] += self.vbyte_encode(diff)
                last[word] = docs_id
        self.avg_doc_len = sum(self.doc_len) / max(self.num_docs, 1)

    def tokenize(self, docs: list[str]) -> Iterator[list[str]]:
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
