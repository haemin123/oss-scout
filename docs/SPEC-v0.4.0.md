# OSS Scout v0.4.0 기획서 — Feature Injection & Safety Hardening

**작성일:** 2026-04-15
**작성자:** haemin123
**현재 버전:** v0.3.1 (Assembly Engine)
**목표 버전:** v0.4.0
**코드네임:** Precision Surgeon

---

## 1. 배경 및 동기

### 1.1 현재 한계

OSS Scout v0.3.x는 **"처음부터 프로젝트를 만드는"** 시나리오에 최적화되어 있다.
하지만 실제 개발자는 대부분 **이미 진행 중인 프로젝트**에서 작업하며,
그 프로젝트에 **특정 기능만 추가**해야 하는 경우가 훨씬 많다.

| 시나리오 | 현재 지원 | v0.4.0 목표 |
|----------|----------|-------------|
| "Next.js SaaS 템플릿 찾아줘" | search_boilerplate | 유지 |
| "이 레포에서 auth 부분만 빼줘" | extract_component | 유지 |
| "내 프로젝트에 Stripe 결제 추가해줘" | **미지원** | search_feature + inject_feature |
| "프론트-백 스키마가 맞는지 확인해줘" | **미지원** | validate_schema |

### 1.2 2차 테스트 피드백 (MCP-FEEDBACK-REPORT.md)

2026-04-15 평가 테스트에서 MCP 기여도 0%라는 결과가 나왔다.
직접 원인은 MCP 서버 미활성화 + 자동 우회였지만,
MCP를 사용했더라도 드러났을 구조적 문제 3가지가 확인되었다:

| 문제 | 근본 원인 | 필요한 조치 |
|------|-----------|-------------|
| Clerk 의존성 미감지 | `check_env`를 안 썼기 때문 (운영 문제) | 스킬 안전장치 (Part B) |
| API URL 경로 불일치 | 프론트-백 URL 매칭 검증 부재 | validate_integration 강화 (Part C) |
| 프론트-백 스키마 불일치 | 스키마 비교 기능 부재 | validate_schema 신규 (Part C) |

---

## 2. 목표

v0.4.0은 3개 파트로 구성된다:

| Part | 이름 | 목표 | 우선순위 |
|------|------|------|----------|
| **A** | Feature Injection | 기존 프로젝트에 기능 코드를 검색하고 주입하는 도구 | P0 |
| **B** | Safety Hardening | MCP 의존 스킬의 안전장치 강화 | P0 |
| **C** | Integration Validation | 프론트-백 통합 검증 강화 | P1 |

---

## 3. Part A: Feature Injection

### 3.1 개요

GitHub Code Search API를 활용하여 **기능 단위 코드**를 검색하고,
사용자의 기존 프로젝트 스택에 맞는 코드를 추출하여
**파일 배치 가이드**와 함께 반환한다.

### 3.2 기존 도구와의 역할 구분

```
[검색 단위별 도구 스펙트럼]

전체 프로젝트              기능 코드 파일            연결 코드 생성
     |                        |                        |
search_boilerplate      search_feature (NEW)     generate_wiring
     |                        |                        |
     v                        v                        v
  scaffold              inject_feature (NEW)       (코드 직접 반환)
  smart_scaffold
```

| 도구 | 검색 대상 | 검색 API | 입력 | 출력 |
|------|-----------|----------|------|------|
| search_boilerplate | GitHub 레포 전체 | Search Repositories API | 자연어 쿼리 | 레포 목록 + 점수 |
| extract_component | 지정 레포 내부 파일 | Contents API | repo_url + 키워드 | 파일 + 의존성 |
| **search_feature** | GitHub 코드 파일 | **Code Search API** | 기능명 + project_dir | 기능 코드 후보 |
| **inject_feature** | 선택된 파일 + 컨텍스트 | Contents API | repo + files + project_dir | 코드 + 배치 가이드 |

### 3.3 신규 도구 #1: search_feature

**목적:** 자연어 기능 요청을 GitHub Code Search 쿼리로 변환하여
사용자 프로젝트 스택과 호환되는 구현 코드를 찾는다.

#### 입력 스키마

```json
{
  "type": "object",
  "properties": {
    "feature": {
      "type": "string",
      "description": "추가하려는 기능 (예: 'stripe payment', 'dark mode', 'auth middleware')"
    },
    "project_dir": {
      "type": "string",
      "description": "기존 프로젝트 디렉토리 경로 (스택 자동 감지용)"
    },
    "language": {
      "type": "string",
      "description": "언어 필터 (생략 시 프로젝트에서 자동 감지)",
      "enum": ["typescript", "javascript", "python"]
    },
    "max_results": {
      "type": "integer",
      "description": "최대 결과 수",
      "default": 5,
      "minimum": 1,
      "maximum": 10
    }
  },
  "required": ["feature", "project_dir"]
}
```

#### 출력 스키마

```json
{
  "feature": "stripe payment",
  "detected_stack": {
    "framework": "nextjs",
    "language": "typescript",
    "db": "prisma",
    "auth": "next-auth"
  },
  "results": [
    {
      "repo": "owner/repo-name",
      "repo_url": "https://github.com/owner/repo-name",
      "stars": 1250,
      "license": "MIT",
      "license_ok": true,
      "quality_score": 82.5,
      "matched_files": [
        {
          "path": "app/api/checkout/route.ts",
          "url": "https://github.com/...",
          "relevance": "high",
          "snippet": "// 첫 5줄 미리보기"
        }
      ],
      "dependencies_needed": ["stripe"],
      "install_command": "npm install stripe",
      "suggested_placement": {
        "app/api/checkout/route.ts": "app/api/checkout/route.ts",
        "lib/stripe.ts": "lib/stripe.ts"
      }
    }
  ]
}
```

#### 처리 파이프라인

```
1. project_dir에서 detect_project_stack() 호출
   → {framework: "nextjs", language: "typescript", ...}

2. feature 키워드를 feature_catalog에서 매칭
   → "stripe payment" → {queries: [...], stack_filters: {...}}
   → 매칭 실패 시 fallback: 사용자 입력을 직접 Code Search 쿼리로 사용

3. 스택에 맞는 GitHub Code Search qualifier 조합
   → "stripe checkout session language:typescript path:app/api"

4. GitHub Code Search API 호출 (search_code)
   → 결과를 레포 단위로 그룹핑

5. 각 레포 그룹에 대해:
   a. 레포 품질 점수 (scoring.py 재사용)
   b. 라이선스 체크 (license_check.py 재사용)
   c. 파일 수 + 의존성 추출

6. 파일 배치 추천 (_suggest_placement)
   → 사용자 프로젝트 구조를 스캔하여 관례적 위치 제안

7. 랭킹 후 상위 N개 반환
```

### 3.4 신규 도구 #2: inject_feature

**목적:** search_feature 결과에서 선택한 코드를 추출하여
사용자 프로젝트에 통합하기 위한 **코드 + 가이드**를 반환한다.
(실제 파일 쓰기는 하지 않음 — Claude Code가 판단하여 적용)

#### 입력 스키마

```json
{
  "type": "object",
  "properties": {
    "repo_url": {
      "type": "string",
      "description": "코드를 추출할 GitHub 레포 URL"
    },
    "feature": {
      "type": "string",
      "description": "기능명 (search_feature 결과와 매칭)"
    },
    "files": {
      "type": "array",
      "items": {"type": "string"},
      "description": "추출할 파일 경로 목록 (search_feature의 matched_files)"
    },
    "project_dir": {
      "type": "string",
      "description": "사용자의 기존 프로젝트 경로"
    },
    "placement": {
      "type": "object",
      "description": "커스텀 파일 배치 (생략 시 자동 추천)",
      "additionalProperties": {"type": "string"}
    }
  },
  "required": ["repo_url", "feature", "files", "project_dir"]
}
```

#### 출력 스키마

```json
{
  "feature": "stripe payment",
  "source_repo": "owner/repo-name",
  "files": [
    {
      "source_path": "app/api/checkout/route.ts",
      "target_path": "app/api/checkout/route.ts",
      "content": "// 전체 파일 내용",
      "is_dependency": false
    },
    {
      "source_path": "lib/stripe.ts",
      "target_path": "lib/stripe.ts",
      "content": "// 전체 파일 내용",
      "is_dependency": true
    }
  ],
  "npm_dependencies": ["stripe", "@stripe/stripe-js"],
  "install_command": "npm install stripe @stripe/stripe-js",
  "env_vars_needed": [
    {
      "name": "STRIPE_SECRET_KEY",
      "description": "Stripe 비밀 키",
      "signup_url": "https://dashboard.stripe.com/apikeys"
    }
  ],
  "integration_notes": "1. app/layout.tsx에 StripeProvider 추가 필요\n2. ...",
  "license": "MIT",
  "conflicts": [
    {
      "target_path": "app/api/checkout/route.ts",
      "reason": "이미 존재하는 파일 — 덮어쓰기 확인 필요"
    }
  ]
}
```

#### 처리 파이프라인

```
1. repo_url 검증 (parse_repo_url 재사용)
2. project_dir 검증 + 스택 감지
3. 지정된 files의 content를 GitHub에서 fetch
   (get_file_content_batch — 병렬 요청)
4. 각 파일의 import 의존성 추출
   (extract_component.py의 _extract_imports_from_content 재사용)
5. 1-depth 의존성 파일 자동 추출
6. placement 결정: 사용자 지정 or _suggest_placement()
7. 충돌 감지: target_path에 이미 파일이 존재하는지 확인
8. 결과 반환 (파일 내용 + 배치 가이드 + 의존성 + 환경변수)
```

### 3.5 Feature Catalog (기능 카테고리 매핑)

자연어 기능 요청을 정밀한 GitHub Code Search 쿼리로 변환하는 매핑 데이터.

#### 초기 지원 기능 (10개)

| # | 기능 ID | 자연어 예시 | 주요 검색 쿼리 |
|---|---------|-------------|---------------|
| 1 | `stripe-payment` | "결제 추가", "Stripe" | `stripe checkout session`, `createPaymentIntent` |
| 2 | `auth-middleware` | "인증 미들웨어", "로그인" | `auth middleware jwt`, `session verify` |
| 3 | `dark-mode` | "다크모드", "테마 전환" | `dark mode toggle`, `useTheme` |
| 4 | `file-upload` | "파일 업로드", "S3" | `upload file s3`, `multer upload` |
| 5 | `i18n` | "다국어", "번역" | `i18n locale`, `useTranslation` |
| 6 | `rate-limiting` | "속도 제한", "Rate limit" | `rate limit middleware`, `rateLimit` |
| 7 | `websocket-chat` | "실시간 채팅", "WebSocket" | `websocket connection`, `socket.io` |
| 8 | `email-send` | "이메일 발송", "SendGrid" | `sendEmail transactional`, `nodemailer` |
| 9 | `pagination` | "페이지네이션", "무한스크롤" | `pagination cursor`, `useInfiniteQuery` |
| 10 | `search-filter` | "검색 필터", "필터링" | `search filter query`, `debounce search` |

#### 스택별 검색 필터

```python
STACK_SEARCH_FILTERS = {
    "nextjs": {
        "language": "typescript",
        "path_hints": ["app/api", "pages/api", "components"],
    },
    "react": {
        "language": "typescript",
        "path_hints": ["src/components", "src/hooks"],
    },
    "express": {
        "language": "typescript",
        "path_hints": ["routes", "middleware", "controllers"],
    },
    "fastapi": {
        "language": "python",
        "path_hints": ["routers", "middleware", "api"],
    },
}
```

### 3.6 파일 배치 추천 규칙 (_suggest_placement)

사용자 프로젝트의 디렉토리 구조를 스캔하여 추출된 코드의 배치 위치를 추천한다.

#### 프레임워크별 기본 배치 규칙

| 프레임워크 | 파일 유형 | 기본 배치 |
|-----------|-----------|-----------|
| Next.js (App Router) | API route | `app/api/{feature}/route.ts` |
| Next.js (App Router) | 컴포넌트 | `components/{feature}/` |
| Next.js (App Router) | 라이브러리 | `lib/{feature}.ts` |
| Next.js (Pages Router) | API route | `pages/api/{feature}.ts` |
| Express | 라우트 | `routes/{feature}.ts` |
| Express | 미들웨어 | `middleware/{feature}.ts` |
| React (CRA/Vite) | 컴포넌트 | `src/components/{feature}/` |
| React (CRA/Vite) | 훅 | `src/hooks/use{Feature}.ts` |
| FastAPI | 라우터 | `routers/{feature}.py` |
| FastAPI | 미들웨어 | `middleware/{feature}.py` |

#### 배치 결정 우선순위

```
1. 사용자가 placement를 직접 지정한 경우 → 그대로 사용
2. 프로젝트에 동일한 이름의 디렉토리가 이미 존재 → 해당 디렉토리 사용
3. 원본 레포의 경로가 사용자 프로젝트 구조와 호환 → 동일 경로 사용
4. 프레임워크별 기본 배치 규칙 적용
```

### 3.7 MCP Prompt: plan_feature_injection

Claude Code에 위임하는 판단 영역:

```
Prompt name: plan_feature_injection
Arguments: feature(str), search_results(str), project_stack(str)

Claude Code가 판단할 내용:
1. 여러 검색 결과 중 어떤 레포의 코드가 가장 적합한지
2. 추출된 코드를 사용자 프로젝트에 어떻게 통합해야 하는지
3. 기존 코드와의 충돌 가능성
4. 추가로 수정해야 할 기존 파일 목록
5. 코드 수정이 필요한 부분 (import 경로, 환경변수명 등)
```

### 3.8 사용자 플로우 예시

```
사용자: "내 Next.js 프로젝트에 Stripe 결제 기능 추가하고 싶어"

Step 1 — 검색
  Claude Code → search_feature(feature="stripe payment", project_dir="./my-app")
  ← 5개 결과 반환 (레포별 관련 파일 + 품질 점수 + 라이선스)

Step 2 — Claude Code가 MCP Prompt로 분석
  Claude Code → plan_feature_injection(feature, results, stack)
  ← "vercel/next-commerce의 checkout 구현이 가장 적합합니다. 이유: ..."

Step 3 — 주입
  Claude Code → inject_feature(
    repo_url="https://github.com/vercel/next-commerce",
    feature="stripe payment",
    files=["app/api/checkout/route.ts", "lib/stripe.ts"],
    project_dir="./my-app"
  )
  ← 파일 내용 + 배치 가이드 + "npm install stripe" + env vars

Step 4 — Claude Code가 사용자 프로젝트에 적용
  (MCP 서버는 관여하지 않음 — Claude Code가 직접 파일 쓰기)
```

---

## 4. Part B: Safety Hardening

### 4.1 문제 정의

`/scout-build` 실행 시 MCP 서버 미활성화 상태에서 사용자 확인 없이
WebSearch로 자동 우회하여 MCP 기여도 평가가 무효화되었다.

### 4.2 조치 1: scout-build 스킬 MCP 사전 검증 (P0)

**수정 대상:** `~/.claude/commands/scout-build.md`

기존 8단계 파이프라인의 0단계와 1단계 사이에 **0.5단계**를 추가한다.

#### 추가할 단계

```markdown
### 0.5단계: MCP 사전 검증 (BLOCKING)

이 단계를 통과하지 않으면 2단계 이후로 진행할 수 없다.

1. ToolSearch로 "search_boilerplate" 도구를 검색한다
2. 도구가 발견되면 → 1단계로 진행
3. 도구가 없으면:
   a. /scout-on 스킬을 실행하여 MCP 서버를 활성화한다
   b. 10초 대기 후 다시 ToolSearch로 확인한다
4. 그래도 없으면:
   a. 사용자에게 보고: "OSS Scout MCP 서버에 연결할 수 없습니다"
   b. 선택지 제시:
      - "MCP 문제를 먼저 해결하기"
      - "WebSearch로 대체하여 진행하기 (MCP 미사용)"
   c. 사용자 응답을 기다린다
5. **절대 자동으로 대체 수단을 사용하지 않는다**
```

### 4.3 조치 2: 도구 우회 정책 규칙 추가 (P1)

**수정 대상:** `~/.claude/rules/artibot/agent-coordination.md`

#### 추가할 규칙

```markdown
## 도구 우회 정책

스킬이 특정 도구(MCP 등)에 의존하는 경우, 해당 도구 없이 자동 우회하지 않는다.

| 상황 | 행동 |
|------|------|
| 도구 실패 + 일반 작업 | 대체 수단 사용 가능. 단, 사용자에게 알린다 |
| 도구 실패 + 도구가 작업의 핵심 | 사용자에게 보고 후 승인받고 진행 |
| 도구 실패 + 도구 평가/테스트 목적 | **반드시 보고 + 승인. 자동 우회 절대 금지** |

원칙:
- 자동 우회 시 편의성을 얻지만 투명성을 잃는다
- 대체 수단을 사용한 경우, 최종 보고에 반드시 "MCP 미사용" 명시
- scout-* 계열 스킬은 모두 MCP 의존 — 자동 우회 금지 대상
```

### 4.4 조치 3: scout 계열 공통 게이트 (P2)

**수정 대상:** `~/.claude/commands/scout-*.md` 전체

모든 scout 스킬의 첫 번째 단계에 공통 MCP 연결 확인 블록을 삽입한다.

```markdown
### 사전 조건: MCP 연결 확인
1. ToolSearch로 "search_boilerplate" 검색
2. 없으면 /scout-on 실행 → 재확인
3. 실패 시 사용자에게 보고 후 중단
```

---

## 5. Part C: Integration Validation 강화

### 5.1 문제 정의

프론트-백 연결 시 발생하는 2가지 불일치를 사전 감지하지 못했다:
1. API URL 경로 불일치 (`/api/reports/` vs `/api/v1/reports/`)
2. 프론트엔드 타입과 백엔드 스키마 불일치

### 5.2 validate_integration 강화: API URL 매칭 (P1)

**수정 대상:** `server/tools/integration_check.py`

기존 5개 검증 항목에 **6번째 검증**을 추가한다.

#### 6. API URL 일관성 검증 (check_api_url_consistency)

```
검증 대상:
- 프론트엔드 코드에서 fetch/axios/useSWR/useQuery 등의 URL 패턴 추출
- 백엔드 코드에서 라우트 정의 (@app.get, router.get, Route 등) 추출
- 양쪽 URL을 대조하여 불일치 감지

추출 패턴 (프론트엔드):
  - fetch("/api/...")
  - axios.get("/api/...")
  - useSWR("/api/...")
  - useQuery({ url: "/api/..." })
  - API_BASE_URL + "/..."

추출 패턴 (백엔드):
  - @app.get("/api/...")           (FastAPI)
  - router.get("/api/...", ...)    (Express)
  - Route("/api/...", ...)         (Starlette)
  - app/api/.../route.ts           (Next.js App Router — 파일 기반)

출력:
  {
    "type": "api_url_mismatch",
    "severity": "error",
    "frontend_url": "/api/reports/",
    "backend_url": "/api/v1/reports/",
    "frontend_file": "src/hooks/useReports.ts:15",
    "backend_file": "routes/reports.ts:8",
    "fix": "URL 경로를 통일하세요"
  }
```

### 5.3 신규 도구: validate_schema (P2)

**목적:** 프론트엔드 TypeScript 타입과 백엔드 스키마(Pydantic/Zod/Prisma)를
비교하여 필드 불일치를 사전 감지한다.

#### 입력 스키마

```json
{
  "type": "object",
  "properties": {
    "project_dir": {
      "type": "string",
      "description": "프로젝트 루트 디렉토리"
    },
    "frontend_types_dir": {
      "type": "string",
      "description": "프론트엔드 타입 정의 경로 (예: src/types/)",
      "default": "auto-detect"
    },
    "backend_schema_dir": {
      "type": "string",
      "description": "백엔드 스키마 경로 (예: server/models/)",
      "default": "auto-detect"
    }
  },
  "required": ["project_dir"]
}
```

#### 검증 항목

```
1. 타입/스키마 파일 탐색
   - 프론트: *.d.ts, types/*.ts, interfaces/*.ts
   - 백엔드: *.py (Pydantic BaseModel), *.ts (Zod schema), schema.prisma

2. 엔티티 이름 매칭
   - 프론트 "Report" 타입 ↔ 백엔드 "Report" 모델 매칭

3. 필드 비교 (이름 + 타입)
   - 프론트에만 있는 필드 → warning
   - 백엔드에만 있는 필드 → info (의도적 숨김 가능)
   - 타입 불일치 (string vs number) → error

4. 출력:
   {
     "matched_entities": [
       {
         "name": "Report",
         "frontend_file": "src/types/report.ts",
         "backend_file": "server/models/report.py",
         "status": "MISMATCH",
         "issues": [
           {
             "field": "created_at",
             "frontend_type": "string",
             "backend_type": "datetime",
             "severity": "warning",
             "note": "직렬화 시 string으로 변환되므로 호환 가능"
           }
         ]
       }
     ],
     "unmatched_frontend": ["ReportFilter"],
     "unmatched_backend": ["ReportInternal"]
   }
```

#### 한계 및 범위 제한

- **정적 분석만 수행** — AST 파싱 없이 정규식 기반 추출
- **100% 정확도를 목표로 하지 않음** — false positive 허용, Claude Code가 최종 판단
- **지원 스택**: TypeScript ↔ Pydantic, TypeScript ↔ Zod, TypeScript ↔ Prisma
- **중첩 타입, 제네릭은 1-depth만** 지원

---

## 6. 구현 계획

### 6.1 Phase 1: 인프라 (P0)

| Step | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 1.1 | GitHubClient에 `search_code()` 메서드 추가 | `github_client.py` | 없음 |
| 1.2 | GitHubClient에 `get_file_content_batch()` 메서드 추가 | `github_client.py` | 없음 |
| 1.3 | scout-build에 MCP 사전 검증 0.5단계 추가 | `scout-build.md` | 없음 |
| 1.4 | agent-coordination.md에 우회 정책 추가 | `agent-coordination.md` | 없음 |

**1.1~1.2는 병렬, 1.3~1.4는 병렬 진행 가능**

### 6.2 Phase 2: search_feature 핵심 (P0)

| Step | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 2.1 | feature_catalog.py 작성 (10개 기능 매핑) | `tools/feature_catalog.py` (신규) | 없음 |
| 2.2 | search_feature.py 핸들러 구현 | `tools/search_feature.py` (신규) | 1.1, 2.1 |
| 2.3 | _suggest_placement() 로직 구현 | `tools/search_feature.py` 내부 | 2.2 |
| 2.4 | 단위 테스트 작성 | `tests/test_search_feature.py` (신규) | 2.2 |

### 6.3 Phase 3: inject_feature + Prompt (P0)

| Step | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 3.1 | inject_feature.py 핸들러 구현 | `tools/inject_feature.py` (신규) | 1.2, 2.3 |
| 3.2 | MCP Prompt plan_feature_injection 추가 | `main.py` | 2.2 |
| 3.3 | main.py에 신규 도구 2개 등록 | `main.py` | 3.1, 3.2 |
| 3.4 | 단위 테스트 작성 | `tests/test_inject_feature.py` (신규) | 3.1 |

### 6.4 Phase 4: Integration Validation 강화 (P1)

| Step | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 4.1 | check_api_url_consistency() 구현 | `tools/integration_check.py` | 없음 |
| 4.2 | 단위 테스트 추가 | `tests/test_integration_check.py` | 4.1 |

### 6.5 Phase 5: 스키마 검증 + 마무리 (P2)

| Step | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 5.1 | validate_schema.py 핸들러 구현 | `tools/validate_schema.py` (신규) | 없음 |
| 5.2 | main.py에 도구 등록 | `main.py` | 5.1 |
| 5.3 | Pydantic 모델 추가 | `models.py` | Phase 2, 3 |
| 5.4 | version.py 업데이트 + CLAUDE.md 갱신 | `version.py`, `CLAUDE.md` | 전체 |
| 5.5 | 단위 테스트 작성 | `tests/test_validate_schema.py` (신규) | 5.1 |

---

## 7. 신규/수정 파일 목록

### 신규 파일 (7개)

| 파일 | Part | 목적 |
|------|------|------|
| `server/tools/feature_catalog.py` | A | 기능 → 검색 쿼리 매핑 데이터 |
| `server/tools/search_feature.py` | A | search_feature 도구 핸들러 |
| `server/tools/inject_feature.py` | A | inject_feature 도구 핸들러 |
| `server/tools/validate_schema.py` | C | validate_schema 도구 핸들러 |
| `tests/test_search_feature.py` | A | search_feature 테스트 |
| `tests/test_inject_feature.py` | A | inject_feature 테스트 |
| `tests/test_validate_schema.py` | C | validate_schema 테스트 |

### 수정 파일 (7개)

| 파일 | Part | 변경 내용 |
|------|------|-----------|
| `server/core/github_client.py` | A | search_code(), get_file_content_batch() 추가 |
| `server/main.py` | A, C | 도구 3개 + MCP Prompt 1개 등록 |
| `server/models.py` | A, C | Pydantic 모델 추가 |
| `server/tools/integration_check.py` | C | API URL 일관성 검증 추가 |
| `server/version.py` | - | 버전 업데이트 (v0.4.0) |
| `CLAUDE.md` | - | Directory Structure 갱신 |
| `tests/test_integration_check.py` | C | API URL 검증 테스트 추가 |

### 스킬 수정 (비코드)

| 파일 | Part | 변경 내용 |
|------|------|-----------|
| `~/.claude/commands/scout-build.md` | B | 0.5단계 MCP 사전 검증 추가 |
| `~/.claude/rules/artibot/agent-coordination.md` | B | 도구 우회 정책 추가 |

---

## 8. 리스크 및 완화

| ID | 리스크 | 영향 | 확률 | 완화 방안 |
|----|--------|------|------|-----------|
| R1 | GitHub Code Search API rate limit (30 req/min) | High | Medium | LocalCache 캐싱 (TTL 24h), 레포 그룹핑으로 API 최소화 |
| R2 | Code Search 결과 품질 (노이즈) | Medium | High | feature_catalog 사전 쿼리, 레포 품질/라이선스 필터, Claude Code에 최종 판단 위임 |
| R3 | 추출 코드-프로젝트 호환성 | High | Medium | detect_project_stack 사전 경고, integration_notes 제공, 직접 파일 쓰기 안 함 |
| R4 | validate_schema 정규식 파싱 정확도 | Medium | High | 1-depth만 지원, false positive 허용, Claude Code가 최종 판단 |
| R5 | feature_catalog 커버리지 부족 | Low | Medium | fallback으로 사용자 입력 직접 검색, 점진적 카탈로그 확장 |

---

## 9. 성공 기준

### Part A: Feature Injection

- [ ] search_feature로 "stripe payment" 검색 시 관련 코드 5개 이상 반환
- [ ] 사용자 프로젝트 스택 자동 감지 후 호환 결과만 필터링
- [ ] inject_feature로 코드 + 배치 가이드 + 의존성 + 환경변수 반환
- [ ] 라이선스/품질 점수 포함
- [ ] 실제 파일 쓰기 없이 Claude Code에 데이터만 전달

### Part B: Safety Hardening

- [ ] MCP 미활성화 상태에서 /scout-build 실행 시 자동 우회 대신 사용자에게 보고
- [ ] /scout-on 자동 실행 후 재확인 성공 시 정상 진행

### Part C: Integration Validation

- [ ] `/api/reports/` vs `/api/v1/reports/` 같은 URL 불일치 감지
- [ ] 프론트 타입 ↔ 백엔드 스키마 필드 불일치 감지 (1-depth)

### 전체

- [ ] 신규 도구 3개 + 기존 도구 강화 1개
- [ ] 테스트 커버리지 90%+ (핵심 로직)
- [ ] ruff check / mypy --strict 에러 0

---

## 10. v0.4.0 이후 로드맵 (참고)

| 버전 | 기능 | 설명 |
|------|------|------|
| v0.4.1 | feature_catalog 확장 | 20개 → 50개 기능 패턴 |
| v0.4.2 | AST 기반 스키마 파싱 | 정규식 → tree-sitter로 정확도 향상 |
| v0.5.0 | 자동 코드 적응 | 추출 코드의 import 경로를 사용자 프로젝트에 맞게 자동 변환 |
| v0.5.0 | requires_mcp 메타데이터 | 스킬 시스템에 의존성 선언 + 게이트 훅 |

---

*이 기획서는 MCP-FEEDBACK-REPORT.md의 분석 결과와 Feature Injection 아이디어를 통합하여 작성되었습니다.*
