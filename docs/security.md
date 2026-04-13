# OSS Scout MCP Server - Security Threat Model & Policy

> Version: 1.0  
> Date: 2026-04-13  
> Status: Draft  
> Scope: OSS Scout MCP Server 전체 시스템

---

## 목차

1. [위협 모델 (STRIDE)](#1-위협-모델-stride)
2. [시크릿 관리 정책](#2-시크릿-관리-정책)
3. [입력 검증 정책](#3-입력-검증-정책)
4. [라이선스 보안](#4-라이선스-보안)
5. [네트워크 보안](#5-네트워크-보안)
6. [운영 보안](#6-운영-보안)
7. [보안 체크리스트 (Phase별)](#7-보안-체크리스트-phase별)

---

## 1. 위협 모델 (STRIDE)

### 1.1 Spoofing (위장) — GitHub API 인증

| 항목 | 내용 |
|------|------|
| **위협 수준** | HIGH |
| **위협** | 탈취된 GitHub Token을 사용한 인증 위장, 또는 미인증 상태에서 API 호출 시도 |
| **공격 시나리오** | 1) 하드코딩된 토큰이 소스에 노출되어 제3자가 Rate Limit을 소진하거나 프라이빗 리소스에 접근 2) 환경 변수가 컨테이너 로그에 유출 |
| **영향** | GitHub API 무단 사용, Rate Limit 소진, 잠재적 프라이빗 레포 접근 |
| **대응** | |

- GitHub Token은 반드시 GCP Secret Manager에서 런타임 로드
- Token에 최소 권한 부여: `public_repo` (read-only) scope만 허용
- Token rotation 주기: 90일
- 서버 시작 시 Token 유효성 검증 (`GET /user` 호출)
- Token이 없거나 무효한 경우 서버 시작을 차단하고 명확한 에러 로그 출력
- 인증 실패(401) 응답 시 즉시 알림 + 자동 Token rotation 트리거

### 1.2 Tampering (변조) — Tarball 다운로드 무결성

| 항목 | 내용 |
|------|------|
| **위협 수준** | HIGH |
| **위협** | `scaffold` 툴의 tarball 다운로드 과정에서 중간자(MITM) 공격으로 파일 변조 |
| **공격 시나리오** | 1) DNS 스푸핑으로 가짜 tarball 서빙 2) 다운로드 중 패킷 변조 3) 악성 레포를 정상 레포로 위장 |
| **영향** | 사용자 환경에 악성 코드 배포, 공급망 공격 |
| **대응** | |

- HTTPS 전용 다운로드 강제 (HTTP URL 거부)
- GitHub API에서 제공하는 tarball URL만 사용 (`api.github.com/repos/{owner}/{repo}/tarball/{ref}`)
- 다운로드 URL은 `github.com` 또는 `api.github.com` 도메인만 허용 (allowlist)
- 다운로드 파일 크기 상한: 100MB (초과 시 거부)
- 다운로드 타임아웃: 30초
- 압축 해제 전 tarball 파일 헤더 검증
- 압축 해제 시 symlink 추출 금지 (path traversal 방지)
- 압축 해제 대상 파일 수 상한: 10,000개

### 1.3 Repudiation (부인) — 로깅 전략

| 항목 | 내용 |
|------|------|
| **위협 수준** | MEDIUM |
| **위협** | 악의적 사용 추적 불가, 보안 사고 발생 시 원인 분석 불능 |
| **공격 시나리오** | 공격자가 서버를 통해 악성 레포를 스캐폴딩한 후 추적 불가 |
| **영향** | 감사 추적 실패, 사고 대응 지연 |
| **대응** | |

- 모든 MCP 툴 호출에 대해 구조화된 로그 기록:
  - 타임스탬프 (UTC, ISO 8601)
  - 툴 이름
  - 입력 파라미터 (민감 정보 마스킹)
  - 실행 결과 (성공/실패)
  - 소요 시간
  - 클라이언트 식별자 (가용한 경우)
- `scaffold` 호출 시 추가 로그: 대상 레포, 타겟 경로, 생성된 파일 수
- 로그는 Cloud Logging으로 전송, 보존 기간 90일
- 보안 이벤트(인증 실패, Rate Limit 초과, 차단된 입력)는 별도 severity `WARNING` 이상으로 기록

### 1.4 Information Disclosure (정보 노출) — 시크릿 & 캐시 데이터

| 항목 | 내용 |
|------|------|
| **위협 수준** | HIGH |
| **위협** | GitHub Token, GCP 인증 정보, Vertex AI 키가 로그/캐시/에러 메시지로 유출 |
| **공격 시나리오** | 1) 에러 스택 트레이스에 토큰 포함 2) Firestore 캐시에 민감 데이터 저장 3) MCP 응답에 내부 정보 누출 |
| **영향** | 인증 정보 탈취, 시스템 내부 구조 노출 |
| **대응** | |

- 에러 응답은 사용자 대면 메시지(한국어)와 내부 디버그 로그(영어)를 분리
- 스택 트레이스는 절대 MCP 응답에 포함하지 않음
- 로그에서 다음 패턴 자동 마스킹:
  - `GITHUB_TOKEN` → `ghp_***`
  - `Authorization` 헤더 값 → `Bearer ***`
  - GCP 서비스 계정 키 → `***`
- Firestore 캐시에는 API 응답 데이터만 저장 (인증 정보 저장 금지)
- Firestore 보안 규칙: 서비스 계정만 접근 허용
- MCP 에러 응답 형식 표준화:
  ```python
  {"error": "사용자 친화적 메시지", "code": "ERROR_CODE"}
  # 절대 포함하지 않음: traceback, token, internal path
  ```

### 1.5 Denial of Service (서비스 거부) — Rate Limit 악용 & 리소스 소진

| 항목 | 내용 |
|------|------|
| **위협 수준** | MEDIUM |
| **위협** | 과도한 요청으로 GitHub API Rate Limit 소진 또는 서버 리소스 고갈 |
| **공격 시나리오** | 1) 반복적 검색 요청으로 GitHub Rate Limit 소진 2) 대용량 tarball 다운로드로 메모리/디스크 고갈 3) 동시 다발 LLM 호출로 Vertex AI 비용 폭증 |
| **영향** | 서비스 불가, 비용 폭증, 다른 사용자 영향 |
| **대응** | |

- GitHub Rate Limit 보호:
  - 남은 호출 수 10 미만 시 캐시 폴백 (스펙 요구사항)
  - Rate Limit 헤더 모니터링: `X-RateLimit-Remaining`, `X-RateLimit-Reset`
  - 429 응답 시 지수 백오프 (1s, 2s, 4s, 최대 3회)
- 서버 레벨 Rate Limiting:
  - 클라이언트당 분당 최대 30회 요청
  - `scaffold` 호출: 클라이언트당 분당 최대 5회
- 리소스 제한:
  - tarball 다운로드: 최대 100MB
  - 압축 해제 파일: 최대 10,000개
  - LLM 동시 호출: 세마포어 10개 (스펙 요구사항)
  - MCP 응답 타임아웃: 30초 (스펙 요구사항)
- Firestore 캐시 TTL 24시간으로 중복 호출 감소
- Cloud Run 인스턴스 최대 수 제한 (autoscaling max-instances)

### 1.6 Elevation of Privilege (권한 상승) — Path Traversal & 권한 상승

| 항목 | 내용 |
|------|------|
| **위협 수준** | CRITICAL |
| **위협** | `scaffold` 툴의 `target_dir` 파라미터를 이용한 path traversal로 시스템 파일 덮어쓰기 |
| **공격 시나리오** | 1) `target_dir: "../../etc/cron.d/malicious"` 2) `target_dir: "/root/.ssh/"` 3) `subdir` 파라미터에 `../` 포함 4) tarball 내 symlink를 통한 디렉토리 탈출 |
| **영향** | 임의 파일 쓰기, 시스템 장악, 자격 증명 탈취 |
| **대응** | |

- `target_dir` 검증 (아래 [입력 검증 정책](#3-입력-검증-정책) 참조):
  ```python
  resolved = Path(target_dir).resolve()
  cwd = Path.cwd().resolve()
  if not resolved.is_relative_to(cwd):
      raise SecurityError("target_dir must be under current working directory")
  ```
- `subdir` 파라미터: `..` 세그먼트 포함 시 즉시 거부
- tarball 압축 해제 시:
  - 모든 파일 경로에서 `..` 세그먼트 검사
  - symlink 추출 거부
  - 절대 경로 시작 파일 거부
- 기존 비어있지 않은 디렉토리 덮어쓰기 금지
- 컨테이너 내 non-root 사용자로 실행 (UID 1000)

---

## 2. 시크릿 관리 정책

### 2.1 시크릿 로딩 우선순위

```
1. GCP Secret Manager (프로덕션 환경, 최우선)
2. 환경 변수 (CI/CD, 로컬 테스트)
3. .env 파일 (로컬 개발 전용)
```

구현 패턴:

```python
import os
from google.cloud import secretmanager

def load_secret(name: str) -> str:
    """시크릿 로딩: Secret Manager -> env var -> .env 순서."""
    # 1. GCP Secret Manager
    if os.getenv("GCP_PROJECT_ID"):
        try:
            client = secretmanager.SecretManagerServiceClient()
            resource = f"projects/{os.environ['GCP_PROJECT_ID']}/secrets/{name}/versions/latest"
            response = client.access_secret_version(request={"name": resource})
            return response.payload.data.decode("utf-8")
        except Exception:
            pass  # fallback to env var

    # 2. Environment variable
    value = os.getenv(name)
    if value:
        return value

    raise EnvironmentError(f"Required secret '{name}' not found")
```

### 2.2 관리 대상 시크릿

| 시크릿 이름 | 용도 | Secret Manager 키 |
|-------------|------|-------------------|
| `GITHUB_TOKEN` | GitHub API 인증 | `oss-scout-github-token` |
| GCP 서비스 계정 | Vertex AI, Firestore | Workload Identity (토큰 불필요) |

### 2.3 금지 사항

| 금지 행위 | 탐지 방법 | 위반 시 조치 |
|-----------|----------|-------------|
| 소스 코드에 시크릿 하드코딩 | `ruff` 커스텀 룰 + pre-commit hook | 커밋 차단 |
| 로그에 시크릿 출력 | 로그 필터 + 코드 리뷰 | 즉시 수정 |
| `.env` 파일 커밋 | `.gitignore` 규칙 + pre-commit hook | 커밋 차단 |
| 시크릿을 MCP 응답에 포함 | 응답 필터링 | 서버 에러 반환 |
| 시크릿을 캐시에 저장 | 코드 리뷰 | 즉시 수정 |

### 2.4 `.env.example` 관리 규칙

- `.env.example`에는 **키 이름만** 기재, 값은 비워두거나 플레이스홀더 사용
- 실제 값, 더미 값 중 실제처럼 보이는 값(예: `ghp_abc123`) 기재 금지
- 새 시크릿 추가 시 반드시 `.env.example` 동시 업데이트
- 형식:
  ```
  GITHUB_TOKEN=
  GCP_PROJECT_ID=
  GCP_REGION=asia-northeast3
  ```

### 2.5 Pre-commit Secret Detection

`.pre-commit-config.yaml`에 다음 훅 추가:

```yaml
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.0
    hooks:
      - id: gitleaks
```

탐지 패턴:
- `ghp_` (GitHub Personal Access Token)
- `ghs_` (GitHub App Installation Token)
- `AIza` (Google API Key)
- `sk-` (일반적인 API key prefix)
- Base64 인코딩된 긴 문자열 (20자 이상)

---

## 3. 입력 검증 정책

### 3.1 `scaffold` 툴 — Path Traversal 방지

**위협 수준**: CRITICAL

```python
import os
from pathlib import Path

def validate_target_dir(target_dir: str, cwd: str) -> Path:
    """target_dir이 CWD 하위인지 검증."""
    # 1. 경로 정규화 (resolve symlinks, normalize ..)
    resolved = Path(target_dir).resolve()
    cwd_resolved = Path(cwd).resolve()

    # 2. CWD 하위 경로인지 확인
    if not resolved.is_relative_to(cwd_resolved):
        raise ValueError(
            f"target_dir must be under current working directory. "
            f"Got: {resolved}, CWD: {cwd_resolved}"
        )

    # 3. 디렉토리가 이미 존재하면 비어있어야 함
    if resolved.exists() and any(resolved.iterdir()):
        raise ValueError(
            f"target_dir already exists and is not empty: {resolved}"
        )

    return resolved


def validate_subdir(subdir: str | None) -> str | None:
    """subdir에 path traversal 시도가 없는지 검증."""
    if subdir is None:
        return None

    # '..' 세그먼트 금지
    parts = Path(subdir).parts
    if ".." in parts:
        raise ValueError("subdir must not contain '..' segments")

    # 절대 경로 금지
    if Path(subdir).is_absolute():
        raise ValueError("subdir must be a relative path")

    return subdir
```

### 3.2 `repo_url` 형식 검증

**위협 수준**: HIGH

```python
import re
from urllib.parse import urlparse

ALLOWED_HOSTS = {"github.com", "www.github.com"}
REPO_URL_PATTERN = re.compile(
    r"^https://github\.com/[a-zA-Z0-9\-_.]+/[a-zA-Z0-9\-_.]+/?$"
)

def validate_repo_url(repo_url: str) -> str:
    """repo_url이 유효한 GitHub 공개 레포 URL인지 검증."""
    # 1. URL 파싱
    parsed = urlparse(repo_url)

    # 2. HTTPS 강제
    if parsed.scheme != "https":
        raise ValueError("repo_url must use HTTPS")

    # 3. 호스트 검증
    if parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("repo_url must be a GitHub URL")

    # 4. 경로 형식 검증 (owner/repo)
    if not REPO_URL_PATTERN.match(repo_url):
        raise ValueError(
            "repo_url must match format: https://github.com/{owner}/{repo}"
        )

    # 5. 쿼리 스트링 / fragment 금지
    if parsed.query or parsed.fragment:
        raise ValueError("repo_url must not contain query parameters or fragments")

    return repo_url.rstrip("/")
```

### 3.3 GitHub Search API 쿼리 인젝션 방지

**위협 수준**: MEDIUM

```python
import re

# GitHub Search API 특수 연산자 (주입 방지 대상)
GITHUB_OPERATORS = [
    "user:", "org:", "repo:", "path:", "language:",
    "stars:", "forks:", "size:", "pushed:", "created:",
    "topic:", "topics:", "license:", "is:", "mirror:",
    "archived:", "in:", "fork:", "NOT", "OR", "AND"
]

def sanitize_search_query(query: str) -> str:
    """사용자 쿼리에서 GitHub Search API 연산자 주입을 방지."""
    sanitized = query

    # 1. 길이 제한 (256자)
    if len(sanitized) > 256:
        sanitized = sanitized[:256]

    # 2. 제어 문자 제거
    sanitized = re.sub(r"[\x00-\x1f\x7f]", "", sanitized)

    # 3. 사용자 입력에서 GitHub 연산자 이스케이프
    # 서버가 추가하는 연산자(language:, stars:)와 구분
    for op in GITHUB_OPERATORS:
        # 대소문자 무시하여 연산자 제거
        pattern = re.compile(re.escape(op), re.IGNORECASE)
        sanitized = pattern.sub("", sanitized)

    # 4. 연속 공백 정리
    sanitized = re.sub(r"\s+", " ", sanitized).strip()

    if not sanitized:
        raise ValueError("Search query is empty after sanitization")

    return sanitized
```

### 3.4 Tarball 추출 안전성

**위협 수준**: HIGH

```python
import tarfile
from pathlib import Path

MAX_FILES = 10_000
MAX_TOTAL_SIZE = 100 * 1024 * 1024  # 100MB

def safe_extract_tarball(tar_path: Path, dest: Path) -> int:
    """안전한 tarball 추출. 위험 요소 검사 후 추출."""
    file_count = 0
    total_size = 0

    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar.getmembers():
            # 1. symlink 거부
            if member.issym() or member.islnk():
                raise SecurityError(f"Symlink not allowed: {member.name}")

            # 2. 절대 경로 거부
            if member.name.startswith("/") or member.name.startswith("\\"):
                raise SecurityError(f"Absolute path not allowed: {member.name}")

            # 3. path traversal 거부
            resolved = (dest / member.name).resolve()
            if not resolved.is_relative_to(dest.resolve()):
                raise SecurityError(f"Path traversal detected: {member.name}")

            # 4. 파일 수 제한
            file_count += 1
            if file_count > MAX_FILES:
                raise SecurityError(f"Too many files: exceeds {MAX_FILES}")

            # 5. 총 크기 제한
            if member.isfile():
                total_size += member.size
                if total_size > MAX_TOTAL_SIZE:
                    raise SecurityError(f"Total size exceeds {MAX_TOTAL_SIZE} bytes")

        # 모든 검증 통과 후 추출
        tar.extractall(dest, filter="data")

    return file_count
```

### 3.5 입력 검증 요약 테이블

| 파라미터 | 검증 항목 | 위협 수준 | 거부 조건 |
|----------|-----------|-----------|-----------|
| `target_dir` | Path traversal | CRITICAL | CWD 외부 경로, 비어있지 않은 기존 디렉토리 |
| `subdir` | Path traversal | HIGH | `..` 세그먼트, 절대 경로 |
| `repo_url` | URL injection, SSRF | HIGH | 비-GitHub 도메인, HTTP, 쿼리/프래그먼트 |
| `query` | Search injection | MEDIUM | GitHub 연산자 주입, 256자 초과 |
| tarball 내용 | Zip slip, resource exhaustion | HIGH | symlink, 절대 경로, `..`, 10K+ 파일, 100MB+ |
| `max_results` | Resource exhaustion | LOW | 1~20 범위 외 |
| `min_stars` | Logical abuse | LOW | 음수 |

---

## 4. 라이선스 보안

### 4.1 라이선스 판정 교차 검증

**위협 수준**: MEDIUM

GitHub API의 `license` 필드는 자동 감지 결과이며 부정확할 수 있다. 반드시 교차 검증한다.

```
판정 로직:
1. GitHub API license.spdx_id 확인
2. 레포의 LICENSE 파일 실제 내용 다운로드
3. 두 결과 비교:
   - 일치 → 해당 라이선스로 판정
   - 불일치 → warnings에 기록, LICENSE 파일 내용 우선
   - LICENSE 파일 없음 → category: "none", recommended: false
   - API 필드 없음 + LICENSE 파일 있음 → LICENSE 파일 기준 판정
```

### 4.2 Copyleft 라이선스 경고 메커니즘

| 라이선스 | 카테고리 | 기본 동작 | `allow_copyleft=True` |
|----------|---------|-----------|----------------------|
| MIT, Apache-2.0, BSD-* | permissive | 포함 | 포함 |
| GPL-*, LGPL-*, AGPL-*, MPL-2.0 | copyleft | **제외** | 포함 + 경고 |
| null, NOASSERTION, other | unknown/none | **제외** | **제외** |

경고 메시지 예시:
```
"이 레포는 GPL-3.0 라이선스입니다. 상업적 사용 시 파생 코드 공개 의무가 있습니다.
법률 검토 없이 프로덕션에 사용하지 마세요."
```

### 4.3 LICENSE 파일 보존 의무

- `scaffold` 실행 시 원본 레포의 LICENSE 파일은 **반드시** 보존
- LICENSE 파일이 없는 경우 경고 메시지를 `next_steps`에 포함
- 생성되는 `CLAUDE.md` 상단에 원본 출처와 라이선스 정보 명시:
  ```markdown
  > Scaffolded from [owner/repo](https://github.com/owner/repo)
  > License: MIT
  > Original LICENSE file preserved in project root.
  ```

---

## 5. 네트워크 보안

### 5.1 Cloud Run 인그레스 설정

| 설정 | 값 | 이유 |
|------|------|------|
| **인그레스** | `internal-and-cloud-load-balancing` (프로덕션) / `all` (개발) | 프로덕션에서는 내부 트래픽만 허용 |
| **인증** | `--no-allow-unauthenticated` (HTTP 모드) | IAM 기반 접근 제어 |
| **최소 인스턴스** | 0 | 비용 최적화 |
| **최대 인스턴스** | 5 | 비용 폭증 방지 |
| **요청 타임아웃** | 60초 | MCP 30초 + 버퍼 |
| **동시성** | 10 | 단일 인스턴스당 동시 요청 수 제한 |
| **메모리** | 512Mi | 대용량 tarball 처리 고려 |

### 5.2 HTTPS 강제

- Cloud Run은 기본적으로 HTTPS 종단 처리
- 서버 코드에서 추가 HTTPS 강제 불필요 (Cloud Run 앞단 처리)
- 외부 API 호출 (GitHub, Vertex AI)은 모두 HTTPS 전용
- HTTP URL을 `repo_url`이나 다운로드 URL로 받을 경우 거부

### 5.3 CORS 정책

```python
# HTTP 모드에서만 적용 (stdio 모드에서는 불필요)
CORS_CONFIG = {
    "allow_origins": [],       # MCP는 서버-서버 통신이므로 기본 차단
    "allow_methods": ["POST"], # MCP는 POST만 사용
    "allow_headers": ["Content-Type", "Authorization"],
    "max_age": 3600,
}
```

- MCP 프로토콜은 브라우저에서 직접 호출하지 않으므로 CORS를 최대한 제한
- 필요 시 특정 오리진만 허용 (예: Claude Code 웹 인터페이스)

### 5.4 MCP 인증 (stdio vs HTTP 모드)

| 모드 | 인증 방식 | 설명 |
|------|-----------|------|
| **stdio** | 없음 (로컬 프로세스) | 로컬 머신에서 직접 실행, OS 수준 프로세스 격리에 의존 |
| **HTTP** | IAM + Bearer Token | Cloud Run IAM 인증, 또는 커스텀 API key |

HTTP 모드 인증 구현:
```python
async def authenticate_request(request) -> bool:
    """HTTP 모드에서 요청 인증."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False

    token = auth_header[7:]

    # Cloud Run에서는 IAM이 이미 인증 처리
    # 추가 검증이 필요한 경우 여기에 구현
    return bool(token)
```

---

## 6. 운영 보안

### 6.1 로깅 정책

#### 로그 구조

```python
import logging
import json

class StructuredFormatter(logging.Formatter):
    """구조화된 JSON 로그 포맷 (Cloud Logging 호환)."""
    SENSITIVE_PATTERNS = [
        (r"ghp_[a-zA-Z0-9]{36}", "ghp_***"),
        (r"ghs_[a-zA-Z0-9]{36}", "ghs_***"),
        (r"Bearer [a-zA-Z0-9\-._~+/]+=*", "Bearer ***"),
        (r"AIza[a-zA-Z0-9\-_]{35}", "AIza***"),
    ]

    def format(self, record):
        message = super().format(record)
        # 민감 정보 마스킹
        for pattern, replacement in self.SENSITIVE_PATTERNS:
            message = re.sub(pattern, replacement, message)
        return message
```

#### 로그 수준 정책

| 이벤트 | 로그 수준 | 포함 정보 |
|--------|----------|----------|
| MCP 툴 호출 | INFO | 툴 이름, 파라미터 (마스킹), 소요 시간 |
| 검색 결과 반환 | INFO | 결과 수, 캐시 히트 여부 |
| GitHub API 호출 | DEBUG | URL, 남은 Rate Limit |
| Rate Limit 경고 | WARNING | 남은 호출 수, 리셋 시간 |
| 인증 실패 | WARNING | 실패 유형, 클라이언트 정보 |
| 입력 검증 실패 | WARNING | 거부된 입력 (마스킹), 거부 사유 |
| 서버 에러 | ERROR | 에러 코드, 메시지 (스택 트레이스 내부 전용) |
| 보안 이벤트 | CRITICAL | 이벤트 유형, 상세 정보 |

#### 절대 로그에 포함하면 안 되는 정보

- GitHub Token, API Key, 인증 정보
- `.env` 파일 내용
- 사용자의 파일 시스템 전체 경로 (CWD 상대 경로만 허용)
- MCP 응답의 전체 본문 (요약만 기록)

### 6.2 감사 추적

`scaffold` 툴 실행 시 Firestore에 감사 로그 기록:

```python
audit_record = {
    "timestamp": datetime.utcnow().isoformat(),
    "action": "scaffold",
    "repo": "owner/name",
    "license": "MIT",
    "target_dir_hash": sha256(target_dir),  # 경로 자체 대신 해시
    "files_created": 42,
    "client_id": client_id or "anonymous",
    "status": "success",
}
```

Firestore 컬렉션: `oss_scout_audit`, TTL 없음 (영구 보존).

### 6.3 의존성 취약점 관리

```bash
# CI/CD 파이프라인에 추가
pip-audit --strict --desc on
```

| 정책 | 내용 |
|------|------|
| **스캔 주기** | PR마다 + 주 1회 스케줄 스캔 |
| **CRITICAL/HIGH CVE** | 즉시 수정, 머지 차단 |
| **MEDIUM CVE** | 7일 이내 수정 |
| **LOW CVE** | 다음 릴리즈까지 수정 |
| **도구** | `pip-audit`, GitHub Dependabot |

`pyproject.toml`에 의존성 버전 pin:
```toml
[project]
dependencies = [
    "mcp>=1.0.0,<2.0.0",
    "PyGithub>=2.0.0,<3.0.0",
    "google-cloud-firestore>=2.0.0,<3.0.0",
    "google-cloud-aiplatform>=1.0.0,<2.0.0",
    "google-cloud-secret-manager>=2.0.0,<3.0.0",
]
```

### 6.4 컨테이너 보안

```dockerfile
# Dockerfile 보안 요구사항
FROM python:3.11-slim AS base

# 1. non-root 사용자 생성
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# 2. 최소 패키지만 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 3. 앱 디렉토리 설정
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

# 4. non-root로 실행
USER appuser

# 5. 헬스체크
HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

EXPOSE 8080
CMD ["python", "-m", "server.main"]
```

컨테이너 보안 체크리스트:

| 항목 | 요구사항 |
|------|----------|
| 베이스 이미지 | `python:3.11-slim` (최소 이미지) |
| 실행 사용자 | non-root (UID 1000+) |
| 불필요한 바이너리 | 제거 (`curl`, `wget` 등 미포함) |
| 파일 퍼미션 | 앱 파일 read-only, `/tmp` write 허용 |
| 시크릿 | 이미지에 포함하지 않음 (런타임 주입) |
| 이미지 스캔 | `trivy` 또는 `grype`로 CI에서 스캔 |

---

## 7. 보안 체크리스트 (Phase별)

### Phase 1: 뼈대

- [ ] `.gitignore`에 `.env`, `*.key`, `*.pem`, `__pycache__` 포함
- [ ] `.env.example`에 실제 시크릿 없음 확인
- [ ] `CLAUDE.md`에 시크릿 하드코딩 금지 규칙 명시
- [ ] pre-commit hook 설정 (gitleaks)
- [ ] pyproject.toml에 의존성 버전 범위 지정

### Phase 2: 핵심 기능

- [ ] `github_client.py`: Token을 환경 변수에서 로드 (하드코딩 없음)
- [ ] `github_client.py`: Rate Limit 추적 및 캐시 폴백 구현
- [ ] `github_client.py`: 429/503 지수 백오프 구현
- [ ] `license_check.py`: GitHub API + LICENSE 파일 교차 검증
- [ ] `license_check.py`: copyleft 경고 메커니즘 구현
- [ ] `scoring.py`: 입력 값 범위 검증 (0~1 clamp)
- [ ] `search.py`: 쿼리 인젝션 방지 (sanitize_search_query)
- [ ] `search.py`: `max_results` 범위 검증 (1~20)
- [ ] 단위 테스트에서 보안 검증 케이스 포함

### Phase 3: LLM & 캐시

- [ ] `llm.py`: Vertex AI 인증 정보 하드코딩 없음
- [ ] `llm.py`: LLM 응답 파싱 실패 시 안전한 폴백
- [ ] `llm.py`: 프롬프트에 사용자 시크릿 포함하지 않음
- [ ] `cache.py`: Firestore에 인증 정보 저장하지 않음
- [ ] `cache.py`: 캐시 키에 사용자 입력 그대로 사용하지 않음 (해시 사용)
- [ ] `cache.py`: TTL 24시간 설정 확인
- [ ] Firestore 보안 규칙: 서비스 계정만 접근

### Phase 4: 나머지 툴

- [ ] `scaffold.py`: `target_dir` path traversal 방지 구현
- [ ] `scaffold.py`: `subdir` 검증 (`..` 금지)
- [ ] `scaffold.py`: `repo_url` GitHub URL만 허용
- [ ] `scaffold.py`: tarball 다운로드 HTTPS 강제
- [ ] `scaffold.py`: tarball 크기 제한 (100MB)
- [ ] `scaffold.py`: 압축 해제 시 symlink/절대경로/path traversal 거부
- [ ] `scaffold.py`: 파일 수 제한 (10,000개)
- [ ] `scaffold.py`: 비어있지 않은 디렉토리 덮어쓰기 금지
- [ ] `scaffold.py`: LICENSE 파일 보존 검증
- [ ] `explain.py`: `repo_url` 검증
- [ ] `license.py`: `repo_url` 검증
- [ ] 통합 테스트에서 보안 시나리오 검증

### Phase 5: 배포

- [ ] Dockerfile: non-root 사용자 실행
- [ ] Dockerfile: 최소 이미지 (`python:3.11-slim`)
- [ ] Dockerfile: 시크릿 미포함 확인
- [ ] `cloudrun.yaml`: 인그레스 설정 확인
- [ ] `cloudrun.yaml`: IAM 인증 설정 (HTTP 모드)
- [ ] `cloudrun.yaml`: 최대 인스턴스 제한
- [ ] `cloudrun.yaml`: 요청 타임아웃 설정
- [ ] `cloudrun.yaml`: 메모리/CPU 제한
- [ ] HTTPS 강제 확인
- [ ] CORS 정책 최소화 확인
- [ ] `pip-audit` CI 통합
- [ ] 컨테이너 이미지 스캔 (trivy/grype)
- [ ] Cloud Logging 연동 확인
- [ ] 민감 정보 마스킹 동작 확인
- [ ] 감사 로그 기록 확인
- [ ] GitHub Token rotation 절차 문서화
- [ ] 보안 사고 대응 절차 수립

---

## 부록: 위협 수준 요약

| 위협 | STRIDE | 수준 | 주요 대응 |
|------|--------|------|-----------|
| Path traversal (`target_dir`, `subdir`, tarball) | EoP | CRITICAL | 경로 정규화 + CWD 하위 검증 |
| GitHub Token 노출 | S, ID | HIGH | Secret Manager + 로그 마스킹 |
| Tarball 변조/악성 파일 | T | HIGH | HTTPS 전용 + 파일 검증 |
| SSRF via `repo_url` | EoP | HIGH | GitHub 도메인 allowlist |
| GitHub API Rate Limit 소진 | DoS | MEDIUM | 캐시 + Rate Limiting |
| Search 쿼리 인젝션 | T | MEDIUM | 연산자 이스케이프 |
| 라이선스 오판정 | ID | MEDIUM | 교차 검증 |
| 감사 추적 부재 | R | MEDIUM | 구조화된 로깅 |
| 에러 메시지 정보 노출 | ID | LOW | 사용자/내부 메시지 분리 |

---

## 부록 B: 아키텍처 전환 보안 업데이트 (2026-04-13)

> 이 섹션은 GCP 의존성 제거 후 경량 아키텍처로의 전환에 따른 보안 변경사항을 기록합니다.
> 본문의 원래 위협 모델은 여전히 유효하며, 아래는 변경된 컴포넌트에 대한 보완입니다.

### B.1 Firestore -> SQLite 로컬 캐시

| 항목 | 기존 (Firestore) | 변경 (SQLite) |
|------|------------------|---------------|
| 접근 제어 | GCP IAM + 서비스 계정 | 파일 시스템 퍼미션 |
| 네트워크 노출 | 클라우드 네트워크 | 없음 (로컬 파일) |
| 암호화 | GCP 기본 암호화 | 없음 (디스크 암호화에 의존) |
| 캐시 위치 | Firestore 컬렉션 | `CACHE_DIR` (기본 `~/.oss-scout/cache`) |

**보안 고려사항:**
- SQLite 파일은 `.gitignore`에 `.cache/` 패턴으로 포함
- 캐시에는 공개 GitHub 데이터만 저장 (인증 정보 없음)
- 컨테이너 환경에서는 `/app/.cache`에 저장, non-root 사용자만 접근
- TTL 만료 데이터는 조회 시 자동 삭제

### B.2 GCP Secret Manager -> 환경 변수

| 항목 | 기존 | 변경 |
|------|------|------|
| 시크릿 저장 | GCP Secret Manager | 환경 변수 / `.env` 파일 |
| 자동 rotation | Secret Manager 버전 관리 | 수동 (사용자 책임) |
| 감사 | Cloud Audit Logs | 없음 |

**보안 고려사항:**
- `.env` 파일은 `.gitignore`에 포함 (커밋 차단)
- `.env.example`에는 키 이름만 기재 (값 없음)
- 프로덕션 환경에서는 CI/CD 시크릿 또는 Cloud Run 환경 변수 사용 권장
- GitHub Token scope: `public_repo` (read-only) 최소 권한 유지

### B.3 Vertex AI -> 서브 에이전트 + MCP Prompts

| 항목 | 기존 (Vertex AI) | 변경 (서브 에이전트) |
|------|------------------|---------------------|
| 외부 API 호출 | Vertex AI (GCP) | 없음 (로컬 룰 기반) |
| 비용 | API 호출당 과금 | 0 |
| 비밀키 필요 | GCP 서비스 계정 | 불필요 |
| 결정론성 | 비결정론적 (LLM) | 결정론적 (룰 기반) |

**보안 고려사항:**
- 외부 LLM API 호출 제거로 프롬프트 인젝션 위협 제거
- 서브 에이전트는 순수 룰 기반: 입력 데이터 → 점수/경고 (부작용 없음)
- MCP Prompts는 클라이언트(Claude Code)에서 실행되므로 서버 측 보안 영향 없음

### B.4 validate_repo 툴 보안

`validate_repo` 툴은 `repo_url` 입력을 받아 서브 에이전트 4종을 실행합니다.

| 검증 항목 | 구현 |
|-----------|------|
| `repo_url` 형식 | `https://github.com/{owner}/{repo}` 정규식 검증 |
| SSRF 방지 | GitHub 도메인만 허용 (기존 정책 동일) |
| 리소스 제한 | 세마포어 10개 (기존 github_client 정책) |
| 에러 처리 | 에이전트 실패 시 graceful degradation (다른 에이전트 계속 실행) |

### B.5 업데이트된 위협 수준 요약

| 위협 | 변경 | 현재 수준 |
|------|------|-----------|
| GCP 인증 정보 유출 | **제거됨** (GCP 의존성 없음) | N/A |
| Vertex AI 비용 폭증 | **제거됨** (LLM 호출 없음) | N/A |
| Firestore 데이터 노출 | **제거됨** → SQLite 로컬 파일 | LOW |
| 프롬프트 인젝션 | **제거됨** (서버 측 LLM 없음) | N/A |
| SQLite 캐시 파일 접근 | **신규** | LOW |
| 환경 변수 시크릿 관리 | 기존과 동일 | MEDIUM |
