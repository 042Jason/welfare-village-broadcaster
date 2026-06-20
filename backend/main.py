# -*- coding: utf-8 -*-
"""
복지마을 방송국 — FastAPI 서버 (Railway 배포용)

기능별 엔드포인트:

  [공통]
  GET  /                          - 헬스체크

  [개인형 — 어르신 복지 검색]
  POST /api/personal/search       - 1턴: 프로필+발화 → 검색→자격→안내
  POST /api/personal/feedback     - 2턴: 피드백 → SMTP 민원전달 또는 추가검색

  [마을방송 — 이장님 보조]
  POST /api/broadcast/convert     - 행정공지 → 쉬운말 + 방송스크립트 (한 번에)

  [호환용 — Deprecated, 옛 UI 대응]
  POST /api/welfare/invoke        - mode로 분기
  POST /api/welfare/feedback      - 피드백

UI 호출 예시:
  # 개인형
  fetch("/api/personal/search", {
    method: "POST",
    body: JSON.stringify({
      thread_id: "user-1234",
      user_profile: {age: 78, region: "충청남도 부여군", ...},
      message: "받을 수 있는 복지 알려주세요"
    })
  })

  # 마을방송
  fetch("/api/broadcast/convert", {
    method: "POST",
    body: JSON.stringify({
      thread_id: "broadcast-001",
      notice_text: "2026년 노인 일자리 사업 신청 안내...",
      dialect: "chungcheong"   // optional
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
    description="LangGraph 에이전트 기반 복지 안내 서비스 (개인형 + 마을방송)",
    version="1.1.0",
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


# ========== 공통 스키마 ==========
class UserProfile(BaseModel):
    age: int = Field(..., description="만 나이")
    region: str = Field("", description="거주 지역 (예: '충청남도 부여군')")
    monthly_income: Optional[int] = Field(None, description="월 소득(원)")
    has_disability: bool = False
    is_rural: Optional[bool] = Field(None, description="None이면 region에서 자동 추론")
    is_single_household: bool = False
    is_single_parent: bool = False
    dialect: Optional[str] = Field(None, description="'chungcheong' | 'jeolla' (마을방송용)")


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


# ========== 개인형 모드 스키마 ==========
class PersonalSearchRequest(BaseModel):
    thread_id: str = Field(..., description="같은 사용자의 연속 대화 식별자")
    user_profile: UserProfile
    message: str = Field(..., description="어르신 발화 (예: '받을 수 있는 복지 알려주세요')")


class PersonalSearchResponse(BaseModel):
    mode: Literal["personal"] = "personal"
    results: list[WelfareItem] = Field(default_factory=list, description="자격 충족 복지사업 목록")
    presented_text: str = Field("", description="화면/음성용 친근한 안내문")
    raw_count: int = Field(0, description="검색 단계 원본 건수")


class PersonalFeedbackRequest(BaseModel):
    thread_id: str = Field(..., description="search 호출 때 쓴 것과 동일한 ID")
    feedback: str = Field(..., description="어르신 피드백 (예: '괜찮네요' / '이런 거 말고 손주 학비 지원')")


class PersonalFeedbackResponse(BaseModel):
    mode: Literal["personal"] = "personal"
    satisfaction: str = Field("", description="'satisfied' | 'unsatisfied' | 'needs_more'")
    summary: str = Field("", description="피드백 분석 요약")
    email_sent: bool = Field(False, description="SMTP 민원 발송 여부")
    response_text: str = Field("", description="어르신에게 보여줄 응답")


# ========== 마을방송 모드 스키마 ==========
class BroadcastConvertRequest(BaseModel):
    thread_id: str = Field(..., description="방송 변환 세션 식별자")
    notice_text: str = Field(..., description="원본 행정 공지문 (한자어/외래어 포함)")
    dialect: Optional[Literal["chungcheong", "jeolla", "standard"]] = Field(
        "standard", description="방언 옵션"
    )


class BroadcastConvertResponse(BaseModel):
    mode: Literal["broadcast"] = "broadcast"
    easy_text: str = Field("", description="어르신 친화 쉬운말 변환문")
    broadcast_text: str = Field("", description="30초 마을 스피커용 스크립트 (90~130자)")


# ========== 호환용 스키마 (Deprecated) ==========
class InvokeRequest(BaseModel):
    mode: Literal["personal", "broadcast"]
    thread_id: str
    user_profile: UserProfile
    message: str


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


# ========== 헬퍼 ==========
def _extract_presented(result: dict, mode: str) -> str:
    """그래프 결과에서 사용자에게 보여줄 텍스트 추출"""
    for m in reversed(result.get("messages", [])):
        if m.__class__.__name__ == "AIMessage" and (
            mode == "personal" and "안녕하세요" in m.content
            or mode == "broadcast" and "방송" in m.content
        ):
            return m.content
    return result["messages"][-1].content if result.get("messages") else ""


def _initial_state(mode: str, user_profile: dict, message: str) -> dict: