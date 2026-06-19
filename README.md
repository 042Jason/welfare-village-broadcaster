# 복지마을 방송국 (Welfare Village Broadcaster)

> *"복지의 마지막 한 걸음은 '신청'이 아니라 '도달'이다."*

**LangGraph + Gemini + 공공데이터 2종 + Supabase + Tavily + SMTP**
디지털 약자에게 복지가 먼저 찾아가는 AI 시스템. 2026 AI 챔피언 해커톤 출품작.

---

## 워크플로우 (3개 노트북)

```
┌─────────────────────────────────────────┐
│ 01_ingest_central.ipynb     (~10분)     │
│   행정안전부 공공서비스 + supportConditions  │
│   → welfare_services / welfare_support_conditions │
└──────────────────┬──────────────────────┘
                   ▼
┌─────────────────────────────────────────┐
│ 02_ingest_local.ipynb       (~30분/일)  │
│   한국사회보장정보원 지자체 (목록 + 상세)│
│   ★ resume 내장: 일일 1,000건 한도 자동 처리│
│   ★ 텍스트 기반 자격조건 키워드/연령 추출 │
└──────────────────┬──────────────────────┘
                   ▼
┌─────────────────────────────────────────┐
│ 03_welfare_agent.ipynb      (즉시)      │
│   LangGraph 에이전트 (개인형 / 마을방송) │
│   ★ UI 버튼이 mode 결정, dispatcher 라우팅│
│   ★ Supabase 조회 + 자격매칭 + SMTP 피드백│
└─────────────────────────────────────────┘
```

---

## 사전 준비

### 1. Supabase 프로젝트 생성 & 스키마 적용
1. https://app.supabase.com → 새 프로젝트
2. SQL Editor에 `schema.sql` 전체 붙여넣고 **RUN** (테이블 3개 + 인덱스 + RLS 정책 생성)
3. Project Settings → API에서 `URL` / `service_role key` / `anon key` 복사

### 2. `.env` 설정
```bash
cp .env.example .env
# → 필수 키 채우기 (가이드는 .env.example 주석 참고)
```

### 3. Python 가상환경 + 의존성
```powershell
.\setup_venv.ps1
```

> 💡 **AnySign 등으로 `.env` 편집 불가** 시: 프로젝트 루트에 `API.txt` 파일을 만들고 Gemini 키 한 줄(`AIza...`)만 두면 자동 주입됩니다.

---

## 실행 순서

| 단계 | 노트북 | 소요 | 비고 |
|---|---|---|---|
| 1 | `01_ingest_central.ipynb` | ~10분 | 한 번만 실행 (이후 증분 갱신) |
| 2 | `02_ingest_local.ipynb` | ~30분/일 × 4~5일 | 일일 한도로 자동 분할, 매일 한 번 Run All |
| 3 | `03_welfare_agent.ipynb` | 즉시 | 시연 / 개발 시 자유롭게 실행 |

> 02번이 다 끝나기 전이라도 03번 시연 가능 — 행정안전부 데이터만으로도 동작.

---

## 필요 API

| 카테고리 | 환경변수 | 용도 | 발급 |
|---|---|---|---|
| 🔴 Supabase | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_ANON_KEY` | 데이터 웨어하우스 | https://app.supabase.com |
| 🔴 공공데이터 | `PUBLIC_SERVICE_API_KEY` | 행정안전부 (01) | [data.go.kr/15113968](https://www.data.go.kr/data/15113968/openapi.do) |
| 🔴 공공데이터 | `LOCAL_WELFARE_API_KEY` | 한국사회보장정보원 (02) | [data.go.kr/15108347](https://www.data.go.kr/data/15108347/openapi.do) |
| 🔴 LLM | `GOOGLE_API_KEY` | Gemini 2.5 Flash | https://aistudio.google.com/app/apikey |
| 🟡 보완 검색 | `TAVILY_API_KEY` | 누락된 한시/신규 사업 | https://app.tavily.com |
| 🟡 민원 발송 | `SMTP_*` | 피드백 → 담당자 메일 | Gmail 앱 비밀번호 등 |
| 🟢 관찰성 | `LANGFUSE_*` | 노드별 trace | https://cloud.langfuse.com |

> 두 공공API는 같은 data.go.kr 계정 인증키 하나로 사용 가능.

---

## 데이터 모델

### `welfare_services` (통합 마스터)
- `source` ∈ {`행정안전부`, `지자체`}
- `service_id` UNIQUE — 모든 적재가 이 키 기준 upsert
- 정형화 필드 20+ 개 (서비스명·소관기관·지원내용·신청방법·URL 등)
- 지자체 전용: `region_sido`, `region_sigungu`, `life_stages[]`, `interest_themes[]`
- `raw_data JSONB` 원본 보관 (재처리/디버깅용)

### `welfare_support_conditions` (자격 조건)
- `service_id` FK / UNIQUE
- 행정안전부: `JA****` 코드 → boolean 컬럼 (정확)
- 지자체: 텍스트 키워드/정규식 추출 (휴리스틱)
- 25개 boolean 컬럼: `disabled`, `single_parent`, `single_household`, `income_band_50/75/100/200`, `pregnant`, `farmer`, ...

### `ingestion_runs` (적재 로그)
- `source` / `fetched_count` / `upserted_count` / `error_count`

---

## 03번 에이전트 — 아키텍처

```
[UI 버튼 클릭]
   mode = "personal" or "broadcast"
        │
        ▼
   dispatcher (결정적 라우터, LLM 미사용)
        │
        ├ personal:
        │   welfare_search (Supabase 조회 + Tavily 보완)
        │   ─► eligibility_check (welfare_support_conditions 매칭)
        │   ─► present_results (Gemini가 따뜻하게 정리)
        │   ─► END (1차 응답)
        │
        │   ▼ [사용자 피드백 2턴]
        │
        │   feedback_analyzer (satisfied/needs_more/unsatisfied 분류)
        │      ├ satisfied   → END
        │      └ 그 외       → escalate_email (SMTP) → END
        │
        └ broadcast:
            easy_translate (행정공지 → 어르신 친화 사투리)
            ─► broadcast_script (마을 스피커 30초 TTS 스크립트)
            ─► END
```

### State 인터페이스 (UI ↔ 그래프)
```python
{
  "mode": "personal" | "broadcast",
  "user_profile": {age, region, monthly_income, has_disability, ...},
  "messages": [{"role": "user", "content": "..."}],
}
```

---

## 자격 매칭 규칙 (LLM 환각 없는 결정적 판단)

`welfare_support_conditions` boolean 컬럼을 사용자 프로필과 매칭:
- `age_start <= profile.age <= age_end`
- 소득: `income_band_50/75/100/200/over200` 중 매칭되는 구간 상한과 비교
- `disabled = true` → `profile.has_disability = true` 필요
- `single_parent`, `single_household` 등 가구 조건 동일

→ "받을 수 있다/없다" 판단에 LLM 미개입. Gemini는 결과 안내·방송 생성에만 사용.

---

## 시연 시나리오

1. **개인형 1턴** — 78세 충남 부여 어르신 → `mode=personal` → Supabase 조회 → 자격 매칭 → 결과 친절히 안내
2. **개인형 2턴 (피드백 → SMTP)** — "손주 학비 지원은 없나요?" → `needs_more` 분류 → 민원 담당자 메일 자동 발송 (SMTP 미설정 시 콘솔 미리보기)
3. **마을방송** — 행정 공지문 → 충청도 사투리 변환 → 30초 방송 스크립트

---

## 확장 로드맵

- [ ] **시맨틱 검색**: pgvector로 임베딩 → 키워드 매칭 한계 극복
- [ ] **TTS**: `broadcast_text` → CLOVA Dubbing/ElevenLabs mp3
- [ ] **STT**: 전화 음성 → Whisper → 그래프 invoke
- [ ] **HITL 승인**: SMTP 발송 전 사용자 최종 확인 (`interrupt()`)
- [ ] **장기 메모리**: 어르신별 신청 이력 별도 테이블
- [ ] **위험 감지**: N일 미응답 어르신 → 복지사 자동 알림
- [ ] **MCP 서버화**: Supabase 조회 도구를 FastMCP로 분리

---

## 발표용 한 줄

> **"복지는 신청하는 사람이 아니라, 필요한 사람에게 먼저 찾아가야 합니다."**

## 라이선스

MIT
