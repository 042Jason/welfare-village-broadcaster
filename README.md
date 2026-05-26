# 복지마을 방송국 (Welfare Village Broadcaster)

> "읽지 못해도, 스마트폰을 못 써도, 말 한마디면 복지 혜택을 받을 수 있게."

**LangGraph + Tavily 기반 멀티에이전트** — 디지털 접근성이 낮은 고령층·취약계층에게 복지 혜택을 ‘말로’ 알려주는 AI 시스템.
2026 AI 챔피언 해커톤 출품작.

## 문제

현재 복지 안내는 문자/앱/홈페이지/공문 중심이라 독거노인·농어촌 고령층·디지털 취약계층은 혜택을 모르거나 신청을 포기합니다. 정작 가장 필요한 사람에게 닿지 못합니다.

## 솔루션

6개의 협업 에이전트가 Supervisor 패턴으로 동작합니다.

| 에이전트 | 역할 |
|---|---|
| `supervisor` | 사용자 의도 분류 후 라우팅 (search / eligibility / easy / broadcast / qna / done) |
| `welfare_search` | Tavily로 복지로·정부24·보건복지부 최신 복지 정보 검색 |
| `eligibility_check` | 사용자 프로필(나이·지역·소득·장애·농어촌)로 자격 매칭 (규칙 기반 `@tool`) |
| `easy_translate` | 행정 용어 → 어르신 친화 말투 (사투리 옵션) |
| `broadcast_script` | 마을 방송 스피커용 TTS 스크립트 (30초 분량) |
| `qna_agent` | 전화 상담 시뮬레이션 (음성 챗봇 페르소나) |

## 빠른 시작

### 1. 클론 & 환경 설정

```bash
git clone https://github.com/<YOUR_NAME>/welfare-village-broadcaster.git
cd welfare-village-broadcaster
```

### 2. API 키 준비

`.env.example`을 `.env`로 복사 후 키 입력:

| 키 | 필수 | 발급처 |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | https://platform.openai.com/api-keys |
| `TAVILY_API_KEY` | ✅ | https://app.tavily.com (월 1,000회 무료) |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | 선택 | https://cloud.langfuse.com |

### 3. 가상환경 + Jupyter 커널 설치

**Windows (PowerShell)**
```powershell
.\setup_venv.ps1
```

**macOS / Linux**
```bash
bash setup_venv.sh
```

### 4. 노트북 실행

`welfare_multiagent.ipynb`를 열어 커널을 **"Welfare Multiagent (.venv)"** 로 선택 후 **Run All**.

## 시연 시나리오

1. **자격 확인** — 78세 충청도 거주 어르신 프로필 → "나도 받을 수 있는 복지가 뭐가 있나" → 기초연금·노인일자리·에너지바우처·농어촌 노인돌봄 자동 매칭
2. **쉬운 말 + 방송 변환** — 딱딱한 행정 공지 → 충청도 사투리 마을 방송 스크립트
3. **멀티턴 전화 상담** — `thread_id` 단위 컨텍스트 유지로 자연스러운 후속 질문 처리

## 아키텍처

```
START → supervisor → (LLM 라우팅)
                       ├─→ welfare_search   ┐
                       ├─→ eligibility_check│
                       ├─→ easy_translate   ├─→ supervisor (loop)
                       ├─→ broadcast_script │
                       └─→ qna_agent        ┘
                       └─→ END (done)
```

- **State**: `messages`(add_messages), `user_profile`, `search_results`, `eligible_benefits`, `easy_text`, `broadcast_text`
- **메모리**: `InMemorySaver` (데모) → 운영 시 `SqliteSaver` 교체
- **관찰성**: LangFuse `CallbackHandler` 자동 wiring

## 기술 스택

- **LangChain 1.x** (`init_chat_model`, `@tool`)
- **LangGraph** (`StateGraph`, `add_messages`, conditional edges, checkpointer)
- **Tavily** (`langchain-tavily`) — bokjiro.go.kr / gov.kr 도메인 우선 검색
- **OpenAI** `gpt-4o-mini` (라우팅 + 생성)
- **LangFuse** (옵션) — 노드 단위 트레이싱

## 확장 로드맵

- [ ] **TTS 연동** — ElevenLabs / CLOVA Dubbing → mp3 출력
- [ ] **STT 입력** — Whisper로 전화 음성 입력 → 그래프 invoke
- [ ] **HITL 승인** — SMS/카톡 발송 노드 앞에 `HumanInTheLoopMiddleware`
- [ ] **장기 메모리** — `SqliteSaver` + `InMemoryStore`로 어르신별 프로필 영구 저장
- [ ] **위험 감지** — 며칠간 응답 없는 어르신 자동 감지 → 복지사 알림
- [ ] **MCP 서버화** — 복지 검색/자격 확인 도구를 FastMCP 서버로 분리

## 발표용 한 줄

> **"복지는 신청하는 사람이 아니라, 필요한 사람에게 먼저 찾아가야 합니다."**

## 라이선스

MIT
