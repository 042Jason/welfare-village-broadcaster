# -*- coding: utf-8 -*-
"""
복지마을 방송국 — FastAPI 서버 (Railway 배포용)

엔드포인트:
  GET  /              - 헬스체크
  POST /api/welfare/invoke    - 1턴 (검색→자격→안내) 또는 마을방송 변환
  POST /api/welfare/feedback  - 2턴 (피드백 분류→SMTP 발송)

UI에서 호출 예시:
  fetch("https://your-app.up.railway.app/api/welfare/invoke", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      mode: "personal",
      thread_id: "user-1234",
      user_profile: {age: 78, region: "충청남도 부여군", ...},
      message: "받을 수 있는 복지 알려주세요"
    })
  })
"""
import os
from typing import Any, Dict, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage

from welfare_agent import graph, cfg

app = FastAPI(
    title="복지마을 방송국 API",
    description="LangGraph 에이전트 기반 복지 안내 서비스",
    version="1.0.0",
)

# CORS - 프론트엔드 도메인 추가
_allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== Request/Response 스키마 ==========
class UserProfile(BaseModel):
    age: int = Field(..., description="만 나이")
    region: str = Field("", description="거주 지역 (예: '충청남도 부여군')")
    monthly_income: Optional[int] = Field(None, description="월 소득(원)")
    has_disability: bool = False
    is_rural: Optional[bool] = Field(None, description="None이면 region에서 자동 추론")
    is_single_household: bool = False
    is_single_parent: bool = False
    dialect: Optional[str] = Field(None, description="'chungcheong' | 'jeolla' (마을방송용)")


class InvokeRequest(BaseModel):
    mode: Literal["personal", "broadcast"] = Field(..., description="UI 버튼이 결정")
    thread_id: str = Field(..., description="같은 사용자의 연속 대화 식별자")
    user_profile: UserProfile
    message: str = Field(..., description="사용자 발화 또는 행정공지문")


class WelfareItem(BaseModel):
    service_id: Optional[str] = None
    source: Optional[str] = None
    service_name: Optional[str] = None
    agency_name: Optional[str] = None
    support_content: Optional[str] = None
    apply_method: Optional[str] = None
    detail_url: Optional[str] = None
    target_description: Optional[str] = None
    region_sido: Optional[str] = None
    region_sigungu: Optional[str] = None
    user_type: Optional[str] = None
    service_field: Optional[str] = None
    match_reason: Optional[str] = None
    tavily_augment: Optional[Dict[str, Any]] = None


class InvokeResponse(BaseModel):
    mode: str
    results: list[WelfareItem] = []
    presented_text: str = ""
    easy_text: str = ""
    broadcast_text: str = ""
    raw_count: int = 0


class FeedbackRequest(BaseModel):
    thread_id: str
    feedback: str


class FeedbackResponse(BaseModel):
    satisfaction: str = ""
    summary: str = ""
    email_sent: bool = False
    response_text: str = ""


# ========== 엔드포인트 ==========
@app.get("/")
def health():
    """헬스체크 — Railway가 컨테이너 상태 확인용으로 사용"""
    return {
        "service": "복지마을 방송국",
        "status": "ok",
        "version": "1.0.0",
        "endpoints": [
            "POST /api/welfare/invoke",
            "POST /api/welfare/feedback",
        ],
    }


@app.post("/api/welfare/invoke", response_model=InvokeResponse)
def invoke(req: InvokeRequest):
    """
    1턴 호출 — 개인형 모드: 검색 → 자격매칭 → 안내 생성
                마을방송 모드: 행정공지 → 쉬운말 → 방송스크립트
    """
    initial_state = {
        "mode": req.mode,
        "user_profile": req.user_profile.model_dump(exclude_none=True),
        "messages": [HumanMessage(content=req.message)],
        "raw_services": [], "eligible_benefits": [],
        "presented": False, "satisfaction": "",
        "email_sent": False, "easy_text": "", "broadcast_text": "",
        "feedback_summary": "",
    }
    try:
        result = graph.invoke(initial_state, config=cfg(req.thread_id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Graph invoke failed: {e}")

    presented_text = ""
    for m in reversed(result.get("messages", [])):
        if m.__class__.__name__ == "AIMessage" and (
            req.mode == "personal" and "안녕하세요" in m.content
            or req.mode == "broadcast" and "방송" in m.content
        ):
            presented_text = m.content
            break
    if not presented_text and result.get("messages"):
        presented_text = result["messages"][-1].content

    return InvokeResponse(
        mode=req.mode,
        results=result.get("eligible_benefits", []),
        presented_text=presented_text,
        easy_text=result.get("easy_text", ""),
        broadcast_text=result.get("broadcast_text", ""),
        raw_count=len(result.get("raw_services", [])),
    )


@app.post("/api/welfare/feedback", response_model=FeedbackResponse)
def feedback(req: FeedbackRequest):
    """
    2턴 호출 — 사용자 피드백 분류 + (필요시) SMTP 발송
    같은 thread_id 사용해야 1턴의 컨텍스트가 이어짐
    """
    try:
        result = graph.invoke(
            {"messages": [HumanMessage(content=req.feedback)]},
            config=cfg(req.thread_id),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Graph invoke failed: {e}")

    response_text = ""
    if result.get("messages"):
        response_text = result["messages"][-1].content

    return FeedbackResponse(
        satisfaction=result.get("satisfaction", ""),
        summary=result.get("feedback_summary", ""),
        email_sent=result.get("email_sent", False),
        response_text=response_text,
    )


# ========== 로컬 실행용 ==========
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
