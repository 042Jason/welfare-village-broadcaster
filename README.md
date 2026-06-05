# 복지마을 방송국 (Welfare Village Broadcaster)

> *"복지의 마지막 한 걸음은 '신청'이 아니라 '도달'이다."*

**LangGraph + 공공데이터 2종 + Tavily 멀티에이전트** — 디지털 약자에게 복지가 먼저 찾아가는 AI 시스템.
2026 AI 챔피언 해커톤 출품작.

---

## 문제 정의

대한민국 복지는 **신청주의**다. 받을 자격이 있어도 ① 자격 판별 ② 서류 준비 ③ 직접 방문이라는 3중 부담을 국민이 진다.
2024 디지털정보격차 실태조사에서 고령층은 일반국민 대비 71.4% 수준으로 4대 취약계층 중 최저.
거동이 어려운 장애인·독거노인일수록 이 장벽 앞에서 멈춘다.

**우리는 '신청주의가 만든 사각지대'와 '디지털 약자의 정보 격차'가 겹치는 지점을 문제로 정의한다.**

---

## 솔루션 — 데이터 소스 역할 분담

```
사용자 입력
   │
   ▼
[1차 — 정확·최신 복지 목록] 공공데이터 2종 API (행정안전부 + 한국사회보장정보원)
   │
   ▼
[2차 — 누락 보완·크로스 검증] Tavily 웹검색 (복지로·정부24 우선)
   │
   ▼
[규칙 기반 자격 매칭] supportConditions 응답 → 사용자 프로필 매칭 (LLM 환각 배제)
   │
   ▼
[LLM 생성] 쉬운말 변환 · 상담 흐름 · 방송 스크립트
   │
   ▼
사용자 (전화·마을 스피커·문자 링크)
```

### 6개 협업 에이전트 (Supervisor 패턴)

| 에이전트 | 역할 | 사용 도구 |
|---|---|---|
| `supervisor` | 의도 분류 + 라우팅 | LLM (gpt-4o-mini, temp=0) |
| `welfare_search` | **1차** 공공API 2종 호출 → **2차** Tavily 보완 → 중복 제거 | 공공API + Tavily |
| `eligibility_check` | supportConditions 조회 → 규칙 기반 자격 매칭 | 공공API + 규칙 엔진 |
| `easy_translate` | 행정 용어 → 어르신 친화 말투 (사투리 옵션) | LLM |
| `broadcast_script` | 마을 스피커 TTS용 30초 방송 멘트 | LLM |
| `qna_agent` | 전화 상담 페르소나 멀티턴 | LLM + 멀티턴 메모리 |

---

## 필요 API (5개)

### 🔴 필수 - 데이터

| # | API | 환경변수 | 발급 |
|---|---|---|---|
| 1 | **행정안전부 — 대한민국 공공서비스(혜택)** | `PUBLIC_SERVICE_API_KEY` | [데이터셋](https://www.data.go.kr/data/15113968/openapi.do) → 활용신청 |
| 2 | **한국사회보장정보원 — 지자체 복지서비스** | `LOCAL_WELFARE_API_KEY` | [데이터셋](https://www.data.go.kr/data/15108347/openapi.do) → 활용신청 |

> 두 API는 같은 data.go.kr 계정 키 하나로 호출 가능. `.env`에 같은 값을 두 번 적어도 됩니다.

### 🔴 필수 - AI

| # | 서비스 | 환경변수 | 발급 |
|---|---|---|---|
| 3 | **OpenAI** | `OPENAI_API_KEY` | https://platform.openai.com/api-keys (gpt-4o-mini, 시연 100회 < $2) |
| 4 | **Tavily** | `TAVILY_API_KEY` | https://app.tavily.com (월 1,000회 무료) |

### 🟡 선택

| # | 서비스 | 환경변수 | 용도 |
|---|---|---|---|
| 5 | **LangFuse** | `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | 노드별 trace, 토큰 모니터링 |

---

## 빠른 시작 (Windows)

```powershell
# 1. 클론
git clone https://github.com/042Jason/welfare-village-broadcaster.git
cd welfare-village-broadcaster

# 2. 환경변수
Copy-Item .env.example .env
notepad .env   # 5개 키 입력

# 3. 가상환경 + 패키지 + Jupyter 커널 자동 설치
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup_venv.ps1

# 4. 노트북 실행
code welfare_multiagent.ipynb   # VSCode에서 커널 'Welfare Multiagent (.venv)' 선택 후 Run All
```

macOS / Linux:
```bash
bash setup_venv.sh
```

---

## 시연 시나리오 (노트북에 내장)

1. **자격 확인** — 충남 부여 78세 독거 어르신 → "받을 수 있는 복지 알려줘" → 공공API에서 노인 대상 서비스 조회 → 각 서비스의 `supportConditions` 조회 → 연령/소득/거주조건 매칭 → 가능 복지 리스트
2. **공지 → 방송 변환** — 딱딱한 행정 공지문 → 충청도 사투리 마을 방송 스크립트 (30초 분량)
3. **멀티턴 전화 상담** — `thread_id` 기반 컨텍스트 유지 → 자연스러운 후속 질문 처리

---

## 자격 매칭 엔�