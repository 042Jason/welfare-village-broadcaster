# -*- coding: utf-8 -*-
"""
복지마을 방송국 — FastAPI 서버 (Railway 배포용)

기능별 엔드포인트:
  GET  /                          - 헬스체크

  [개인형 - 어르신 복지 검색]
  POST /api/personal/search       - 1턴: 프로필+발화 → 검색→자격→안내
  POST /api/personal/feedback     - 2턴: 피드백 → SMTP 민원전달

  [마을방송 - 이장님 보조]
  POST /api/broadcast/convert     - 행정공지 → 쉬운말 + 방송스크립트

  [Deprecated]
  POST /api/welfare/invoke        - mode로 분기 (옛 호환)
  POST /api/welfare/feedback      - 피드백 (옛 호환)
"""
import os
from typing import Any, Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage

from welfare_agent import graph, cfg

app = FastAPI(
    title="복지마을 방송국 API",
    description="LangGraph 에이전트 기반 복지 안내 서비스 (개인형 + 마을방송)",
    version="1.1.0",
)

_allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== 공통 스키마 ==========
class UserProfile(BaseModel):
    age: int = Field(..., description="만 나이")
    region: str = Field("", description="거주 지역 (예: '충청남도 부여군')")
    monthly_income: Optional[int] = Field(None, description="월 소득(원)")
    has_disability: bool = False
    is_rural: Optional[bool] = None
    is_single_household: bool = False
    is_single_parent: bool = False
    dialect: Optional[str] = None


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


# ========== 개인형 스키마 ==========
class PersonalSearchRequest(BaseModel):
    thread_id: str
    user_profile: UserProfile
    message: str


class PersonalSearchResponse(BaseModel):
    mode: str = "personal"
    results: List[WelfareItem] = Field(default_factory=list)
    presented_text: str = ""
    raw_count: int = 0


class PersonalFeedbackRequest(BaseModel):
    thread_id: str
    feedback: str


class PersonalFeedbackResponse(BaseModel):
    mode: str = "personal"
    satisfaction: str = ""
    summary: str = ""
    email_sent: bool = False
    response_text: str = ""


# ========== 마을방송 스키마 ==========
class BroadcastConvertRequest(BaseModel):
    thread_id: str
    notice_text: str
    dialect: Optional[str] = "standard"


class BroadcastConvertResponse(BaseModel):
    mode: str = "broadcast"
    easy_text: str = ""
    broadcast_text: str = ""


# ========== Legacy ==========
class InvokeRequest(BaseModel):
    mode: Literal["personal", "broadcast"]
    thread_id: str
    user_profile: UserProfile
    message: str


class InvokeResponse(BaseModel):
    mode: str
    results: List[WelfareItem] = Field(default_factory=list)
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


# ========== Helpers ==========
def _extract_presented(result: dict, mode: str) -> str:
    msgs = result.get("messages", [])
    for m in reversed(msgs):
        if m.__class__.__name__ != "AIMessage":
            continue
        c = m.content
        if mode == "personal" and "안녕하세요" in c:
            return c
        if mode == "broadcast" and "방송" in c:
            return c
    return msgs[-1].content if msgs else ""


def _initial_state(mode: str, user_profile: dict, message: str) -> dict:
    return {
        "mode": mode,
        "user_profile": user_profile,
        "messages": [HumanMessage(content=message)],
        "raw_services": [],
        "eligible_benefits": [],
        "presented": False,
        "satisfaction": "",
        "email_sent": False,
        "easy_text": "",
        "broadcast_text": "",
        "feedback_summary": "",
    }


# ========== Endpoints ==========
@app.get("/")
def health():
    return {
        "service": "복지마을 방송국",
        "status": "ok",
        "version": "1.1.0",
        "endpoints": {
            "personal": [
                "POST /api/personal/search",
                "POST /api/personal/feedback",
            ],
            "broadcast": [
                "POST /api/broadcast/convert",
            ],
            "legacy": [
                "POST /api/welfare/invoke (deprecated)",
                "POST /api/welfare/feedback (deprecated)",
            ],
        },
    }


# ----- 개인형 -----
@app.post("/api/personal/search", response_model=PersonalSearchResponse, tags=["personal"])
def personal_search(req: PersonalSearchRequest):
    initial = _initial_state(
        mode="personal",
        user_profile=req.user_profile.model_dump(exclude_none=True),
        message=req.message,
    )
    try:
        result = graph.invoke(initial, config=cfg(req.thread_id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"검색 실패: {e}")

    return PersonalSearchResponse(
        results=result.get("eligible_benefits", []),
        presented_text=_extract_presented(result, "personal"),
        raw_count=len(result.get("raw_services", [])),
    )


@app.post("/api/personal/feedback", response_model=PersonalFeedbackResponse, tags=["personal"])
def personal_feedback(req: PersonalFeedbackRequest):
    try:
        result = graph.invoke(
            {"messages": [HumanMessage(content=req.feedback)]},
            config=cfg(req.thread_id),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"피드백 처리 실패: {e}")

    msgs = result.get("messages", [])
    return PersonalFeedbackResponse(
        satisfaction=result.get("satisfaction", ""),
        summary=result.get("feedback_summary", ""),
        email_sent=result.get("email_sent", False),
        response_text=msgs[-1].content if msgs else "",
    )


# ----- 마을방송 -----
@app.post("/api/broadcast/convert", response_model=BroadcastConvertResponse, tags=["broadcast"])
def broadcast_convert(req: BroadcastConvertRequest):
    initial = _initial_state(
        mode="broadcast",
        user_profile={"dialect": req.dialect or "standard"},
        message=req.notice_text,
    )
    try:
        result = graph.invoke(initial, config=cfg(req.thread_id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"방송 변환 실패: {e}")

    return BroadcastConvertResponse(
        easy_text=result.get("easy_text", ""),
        broadcast_text=result.get("broadcast_text", ""),
    )


# ----- Legacy -----
@app.post("/api/welfare/invoke", response_model=InvokeResponse, tags=["legacy"])
def invoke_legacy(req: InvokeRequest):
    initial = _initial_state(
        mode=req.mode,
        user_profile=req.user_profile.model_dump(exclude_none=True),
        message=req.message,
    )
    try:
        result = graph.invoke(initial, config=cfg(req.thread_id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Graph invoke failed: {e}")

    return InvokeResponse(
        mode=req.mode,
        results=result.get("eligible_benefits", []),
        presented_text=_extract_presented(result, req.mode),
        easy_text=result.get("easy_text", ""),
        broadcast_text=result.get("broadcast_text", ""),
        raw_count=len(result.get("raw_services", [])),
    )


@app.post("/api/welfare/feedback", response_model=FeedbackResponse, tags=["legacy"])
def feedback_legacy(req: FeedbackRequest):
    try:
        result = graph.invoke(
            {"messages": [HumanMessage(content=req.feedback)]},
            config=cfg(req.thread_id),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Graph invoke failed: {e}")

    msgs = result.get("messages", [])
    return FeedbackResponse(
        satisfaction=result.get("satisfaction", ""),
        summary=result.get("feedback_summary", ""),
        email_sent=result.get("email_sent", False),
        response_text=msgs[-1].content if msgs else "",
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
