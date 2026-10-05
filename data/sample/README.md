# 샘플 데이터

크롤링 없이도 바로 검색해 볼 수 있도록 저장소에 함께 넣어 둔 글.

- `posts/{stock,it,game,sports,food,travel,movie}/` (1~25번): 직접 작성한 가상의 커뮤니티 글
- `posts/wiki/` (26~346번): 한국어 위키백과 문서 321개의 첫 문단(요약)

## 위키백과 출처

- 출처: [한국어 위키백과](https://ko.wikipedia.org) 기여자들. 각 글의 `url`이 원문 문서이며, 기여자 목록은 원문 문서의 "역사" 탭에서 볼 수 있다.
- 라이선스: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/deed.ko). 이 폴더의 위키백과 글도 같은 라이선스를 따른다.
- 변경 사항: 문서 첫 문단만 일반 텍스트로 발췌(MediaWiki API `prop=extracts&exintro&explaintext`)하고, 다른 샘플과 같은 frontmatter 형식으로 저장했다. `created_at`은 원문의 최종 수정 시각이다.
- 가져온 날: 2026-10-05
