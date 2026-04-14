빠른 OSS 보일러플레이트 검색 및 추천을 수행합니다. 20줄 이내로 간결하게 응답합니다.

검색 쿼리: $ARGUMENTS

## 실행 방법

1. `search_boilerplate` 도구로 검색합니다 (max_results=5).
2. 결과를 quality_score 기준으로 정렬합니다.
3. 상위 3개를 간결한 테이블로 보여줍니다.
4. 최우선 추천 1개와 한 줄 이유를 제시합니다.

## 출력 형식 (반드시 20줄 이내)

```
## Quick Scout: {query}

| # | 레포 | Stars | Score | 라이선스 |
|---|------|-------|-------|----------|
| 1 | owner/repo | 1.2k | 0.85 | MIT |
| 2 | owner/repo | 800 | 0.72 | Apache-2.0 |
| 3 | owner/repo | 500 | 0.65 | MIT |

추천: **owner/repo** - {한 줄 이유}

상세: `/scout-explain https://github.com/owner/repo`
시작: `/scout-scaffold https://github.com/owner/repo ./my-project`
```

모든 응답은 한국어로 작성합니다.
