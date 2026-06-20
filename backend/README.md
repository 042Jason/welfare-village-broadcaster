# 복지마을 방송국 — Backend (FastAPI)

LangGraph 에이전트를 Railway에 배포 가능한 FastAPI 서버로 래핑한 백엔드.

## 파일 구조
```
backend/
├── welfare_agent.py    # LangGraph 에이전트 (03번 노트북 기반)
├── main.py             # FastAPI 엔드포인트
├── requirements.txt    # 의존성
├── Procfile            # Railway 시작 명령
├── railway.json        # Railway 빌드 설정
├── .python-version     # Python 3.11
└── .env.example        # 환경변수 템플릿
```

## 로컬 실행

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 환경변수 설정 (.env 파일에)
Copy-Item .env.example .env
notepad .env

# 서버 실행
uvicorn main:app --reload --port 8000
```

→ http://localhost:8000 접속해서 헬스체크 확인
→ http://localhost:8000/docs 에서 Swagger UI로 테스트

## API 엔드포인트

### `POST /api/welfare/invoke` — 1턴
```json
{
  "mode": "personal",
  "thread_id": "user-1234",
  "user_profile": {
    "age": 78,
    "region": "충청남도 부여군",
    "monthly_income": 800000,
    "is_single_household": true
  },
  "message": "받을 수 있는 복지 알려주세요"
}
```

### `POST /api/welfare/feedback` — 2턴 (같은 thread_id 사용)
```json
{
  "thread_id": "user-1234",
  "feedback": "이런 거 말고 손주 학비 지원은 없나요?"
}
```

## Railway 배포

1. https://railway.app → New Project → Deploy from GitHub repo
2. 저장소 선택 → **Root Directory를 `backend`로 설정**
3. Variables 탭에서 `.env.example`의 키들 등록
4. Deploy 클릭 → 약 3~5분 후 도메인 발급 (`https://xxx.up.railway.app`)
5. 헬스체크: `curl https://xxx.up.railway.app/` → `{"service":"복지마을 방송국","status":"ok",...}`
