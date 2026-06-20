# -*- coding: utf-8 -*-
"""
복지마을 방송국 — LangGraph 에이전트 (서버용)

03_welfare_agent.ipynb의 모든 노드와 그래프 정의를 순수 Python으로 정리.
main.py(FastAPI)가 이 모듈의 graph 객체를 import해서 호출.
"""
from __future__ import annotations
import os
import re
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, TypedDict, Annotated
from urllib.parse import unquote

from dotenv import load_dotenv

# ---------- 환경변수 로드 (.env > env_view.txt fallback) ----------
load_dotenv(".env", override=True)
_env_view = Path("env_view.txt")
if _env_view.exists():
    load_dotenv(str(_env_view), override=False)

_api_txt = Path("API.txt")
if _api_txt.exists():
    for _line in _api_txt.read_text(encoding="utf-8", errors="ignore").splitlines():
        _token = _line.strip().split("=")[-1].strip().strip('"').strip("'")
        if _token.startswith("sk-") and len(_token) > 30:
            os.environ.setdefault("OPENAI_API_KEY", _token)
            break

# ---------- Supabase ----------
from supabase import create_client, Client

SB: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_ANON_KEY"],
)

# ---------- LLM ----------
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from pydantic import BaseModel, Field

llm = init_chat_model("openai:gpt-5.5", temperature=0.3)
classifier_llm = init_chat_model("openai:gpt-4o-mini", temperature=0.0)

# ---------- Tavily (선택) ----------
HAS_TAVILY = bool(os.getenv("TAVILY_API_KEY") and not os.getenv("TAVILY_API_KEY", "").startswith("tvly-..."))
tavily_tool = None
if HAS_TAVILY:
    try:
        from langchain_tavily import TavilySearch
        tavily_tool = TavilySearch(
            max_results=5, topic="general",
            include_domains=["bokjiro.go.kr", "gov.kr", "mohw.go.kr", "korea.kr"],
            search_depth="advanced",
        )
    except Exception as e:
        print(f"⚠️ Tavily 비활성: {e}")

HAS_SMTP = all(os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"))

# ============================================================
# 자격 매칭 엔진
# ============================================================
INCOME_BANDS = [
    ("income_band_50",      1_196_000),
    ("income_band_75",      1_794_000),
    ("income_band_100",     2_392_000),
    ("income_band_200",     4_784_000),
    ("income_band_over200", 10**12),
]

def infer_is_rural(region: str) -> bool:
    if not region: return False
    parts = region.split()
    if len(parts) >= 2 and parts[1].endswith("군"): return True
    return False

class UserProfile(BaseModel):
    age: int
    region: str = ""
    monthly_income: Optional[int] = None
    has_disability: bool = False
    is_rural: Optional[bool] = None
    is_single_household: bool = False
    is_single_parent: bool = False

    def model_post_init(self, __context):
        if self.is_rural is None:
            self.is_rural = infer_is_rural(self.region)

def is_eligible(profile: UserProfile, cond: dict) -> tuple[bool, str]:
    if not cond: return True, "지원조건 정보 없음"
    if cond.get("age_start") and profile.age < cond["age_start"]:
        return False, f"연령 {cond['age_start']}세 이상 대상"
    if cond.get("age_end") and profile.age > cond["age_end"]:
        return False, f"연령 {cond['age_end']}세 이하 대상"
    has_income_constraint = any(cond.get(c) for c, _ in INCOME_BANDS)
    if has_income_constraint and profile.monthly_income is not None:
        ok = any(cond.get(c) and profile.monthly_income <= lim for c, lim in INCOME_BANDS)
        if not ok: return False, "소득 구간 초과"
    if cond.get("disabled") and not profile.has_disability:
        return False, "장애인 대상"
    if cond.get("single_parent") and not profile.is_single_parent:
        return False, "한부모/조손 대상"
    if cond.get("single_household") and not profile.is_single_household:
        return False, "1인가구 대상"
    return True, "자격 매칭"

# ============================================================
# SMTP 발송
# ============================================================
def send_complaint_email(subject: str, body: str, to_addr: Optional[str] = None) -> dict:
    to_addr = to_addr or os.getenv("SMTP_TO", "")
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    pw   = os.getenv("SMTP_PASS", "")
    from_name = os.getenv("SMTP_FROM_NAME", "복지마을 방송국")

    msg = MIMEMultipart()
    msg["From"] = formataddr((from_name, user or "noreply@example.com"))
    msg["To"] = to_addr or "민원담당자@미설정"
    msg["Subject"] = subject
    msg["Date"] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0900")
    msg.attach(MIMEText(body, "plain", "utf-8"))

    if not (host and user and pw and to_addr):
        print("📧 [SMTP 미설정 - 미리보기]\n" + body[:500])
        return {"status": "preview", "to": msg["To"], "subject": subject}

    with smtplib.SMTP(host, port) as srv:
        srv.starttls()
        srv.login(user, pw)
        srv.sendmail(user, [to_addr], msg.as_string())
    return {"status": "sent", "to": to_addr, "subject": subject}

# ============================================================
# State
# ============================================================
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import InMemorySaver

class WelfareState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    mode: Literal["personal", "broadcast"]
    user_profile: Dict[str, Any]
    raw_services: List[Dict[str, Any]]
    eligible_benefits: List[Dict[str, Any]]
    presented: bool
    satisfaction: Literal["", "satisfied", "unsatisfied", "needs_more"]
    email_sent: bool
    feedback_summary: str
    easy_text: str
    broadcast_text: str

# ============================================================
# LLM 의도 추출기
# ============================================================
class SearchIntent(BaseModel):
    keywords: List[str] = Field(description="복지 사업명/지원대상에 들어갈 한국어 키워드 5~10개")
    user_type: str = Field(default="", description="사용자구분 (노인/장애인/청년/임산부 등)")
    intent_summary: str = Field(description="사용자 상황 한 줄 요약")

INTENT_PROMPT = """사용자 발화와 프로필을 분석해 복지 검색 키워드와 사용자구분을 추출합니다.
- keywords: 한국어 복지 사업명에 들어갈 단어 5~10개 + 동의어
- user_type: DB 표준값 (노인/장애인/청년/임산부/영유아/다문화/한부모/근로자/구직자/농업인/어업인/학생/저소득) 중 하나, 없으면 빈 문자열
- intent_summary: 한 줄 요약"""

def extract_intent(query: str, profile: dict) -> SearchIntent:
    try:
        structured = classifier_llm.with_structured_output(SearchIntent)
        prof_txt = (
            f"{profile.get('age','?')}세 / 지역={profile.get('region','?')} / "
            f"월소득 {profile.get('monthly_income','?')}원 / "
            f"가구={'1인' if profile.get('is_single_household') else '복수'}"
            f"{', 한부모' if profile.get('is_single_parent') else ''}"
            f"{', 장애' if profile.get('has_disability') else ''}"
        )
        user_msg = "발화: " + query + " / 프로필: " + prof_txt
        return structured.invoke([
            SystemMessage(content=INTENT_PROMPT),
            HumanMessage(content=user_msg),
        ])
    except Exception as e:
        print(f"의도 추출 실패: {e}")
        age = (profile or {}).get("age", 0)
        ut = "노인" if age >= 65 else ("청년" if 18 < age <= 39 else "")
        return SearchIntent(keywords=[], user_type=ut, intent_summary="")

# ============================================================
# 노드 1: welfare_search
# ============================================================
def welfare_search_node(state: WelfareState) -> dict:
    print("📍 [1/3] welfare_search 진입")
    last_user = next((m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None)
    query = last_user.content if last_user else ""
    profile = state.get("user_profile") or {}

    intent = extract_intent(query, profile)
    kws = intent.keywords or []
    user_type = intent.user_type or ""

    region_parts = (profile.get("region") or "").split()
    region_sido    = region_parts[0] if len(region_parts) >= 1 else None
    region_sigungu = region_parts[1] if len(region_parts) >= 2 else None

    def _build_or(filters_keywords):
        f = []
        for kw in filters_keywords:
            if not kw: continue
            f.append(f"service_name.ilike.%{kw}%")
            f.append(f"user_type.ilike.%{kw}%")
            f.append(f"service_field.ilike.%{kw}%")
            f.append(f"target_description.ilike.%{kw}%")
        return ",".join(f)

    select_cols = ("service_id, source, service_name, agency_name, support_content, "
                   "apply_method, detail_url, target_description, region_sido, region_sigungu, "
                   "user_type, service_field")

    services = []
    # 행정안전부
    q = SB.table("welfare_services").select(select_cols).eq("source", "행정안전부")
    or_str = _build_or(kws + ([user_type] if user_type else []))
    if or_str: q = q.or_(or_str)
    q = q.limit(25)
    try: services.extend(q.execute().data or [])
    except Exception as e: print(f"공공 조회 오류: {e}")

    # 지자체
    if region_sido:
        q2 = SB.table("welfare_services").select(select_cols).eq("source", "지자체").eq("region_sido", region_sido)
        if region_sigungu:
            q2 = q2.or_(f"region_sigungu.eq.{region_sigungu},region_sigungu.is.null")
        if or_str: q2 = q2.or_(or_str)
        q2 = q2.limit(25)
        try: services.extend(q2.execute().data or [])
        except Exception as e: print(f"지자체 조회 오류: {e}")

    # 중복 제거
    seen, unique = set(), []
    for s in services:
        nm = s.get("service_name", "")
        if nm and nm not in seen:
            seen.add(nm); unique.append(s)

    # Tavily 하이브리드 (증강 + 한시/신규 보완)
    primary_kw = kws[0] if kws else ""
    augmented_count = 0
    matched_tv_indices = set()
    if tavily_tool and primary_kw and unique:
        try:
            tv_q = f"{primary_kw} {profile.get('region','')} 복지 지원 신청"
            raw = tavily_tool.invoke({"query": tv_q})
            tv_results = (raw.get("results", []) if isinstance(raw, dict) else raw)[:5]

            STOP_TOKENS = {"지원","사업","제도","서비스","대상","신청","안내","정보","비용","수당","수급",
                           "지급","대상자","사례","상담","처리","관리","운영","교육","조례","사례집"}
            def _tokens(s):
                return [t for t in re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", s or "") if t and t not in STOP_TOKENS]

            for s in unique:
                svc_name = s.get("service_name", "")
                if not svc_name: continue
                svc_tokens = _tokens(svc_name)
                if len(svc_tokens) < 2: continue
                best_tv_idx, best_hits = None, 0
                for idx, tv in enumerate(tv_results):
                    tv_text = (str(tv.get("title","")) + " " + str(tv.get("content",""))).strip()
                    hits = sum(1 for t in svc_tokens if t in tv_text)
                    if hits > best_hits:
                        best_hits, best_tv_idx = hits, idx
                if best_tv_idx is not None and best_hits >= 2 and best_hits >= len(svc_tokens) * 0.6:
                    tv = tv_results[best_tv_idx]
                    s["tavily_augment"] = {
                        "snippet": (tv.get("content","") or "")[:250],
                        "url": tv.get("url",""),
                        "title": tv.get("title",""),
                    }
                    augmented_count += 1
                    matched_tv_indices.add(best_tv_idx)

            # 한시/신규 보완
            for idx, tv in enumerate(tv_results):
                if idx in matched_tv_indices: continue
                tv_text = (str(tv.get("title","")) + " " + str(tv.get("content",""))).strip()
                intent_hits = sum(1 for kw in kws if kw and kw in tv_text)
                if intent_hits < 1 or len(tv_text) < 50: continue
                unique.append({
                    "service_id": None, "source": "Tavily(웹보완)",
                    "service_name": (tv.get("title","") or "")[:80],
                    "agency_name": "웹검색 — 한시/신규 사업 후보",
                    "support_content": (tv.get("content","") or "")[:250],
                    "apply_method": "", "detail_url": tv.get("url",""),
                    "target_description": "", "region_sido": region_sido,
                    "region_sigungu": None, "user_type": user_type or "", "service_field": "",
                })
        except Exception as e:
            print(f"Tavily 오류: {e}")

    # 의도 매칭 점수
    def _intent_score(s):
        text = " ".join([str(s.get(k,"")) for k in
                         ["service_name","target_description","support_content","user_type","service_field"]])
        return sum(1 for kw in kws if kw and kw in text)
    for s in unique:
        s["_intent_score"] = _intent_score(s)
    unique.sort(key=lambda s: -s.get("_intent_score", 0))

    by_source = {}
    for s in unique:
        by_source[s.get("source","?")] = by_source.get(s.get("source","?"), 0) + 1
    src_summary = ", ".join([f"{k} {v}건" for k, v in by_source.items()])
    msg = (
        f"🔎 검색 결과: 총 {len(unique)}건  ({src_summary})\n"
        f"   ↳ 의도: {intent.intent_summary or '-'}\n"
        f"   ↳ 키워드: [{', '.join(kws) if kws else '-'}]\n"
        f"   ↳ 필터: 지역={region_sido}/{region_sigungu or '-'}, 사용자유형={user_type or '-'}\n"
        f"   ↳ Tavily 증강: {augmented_count}건"
    )
    return {"raw_services": unique, "messages": [AIMessage(content=msg)]}

# ============================================================
# 노드 2: eligibility_check
# ============================================================
def eligibility_node(state: WelfareState) -> dict:
    print("📍 [2/3] eligibility_check 진입")
    pdict = state.get("user_profile") or {}
    if not pdict.get("age"):
        return {"eligible_benefits": [],
                "messages": [AIMessage(content="자격 확인을 위해 나이/지역/소득 정보가 필요합니다.")]}
    profile = UserProfile(**{k: v for k, v in pdict.items() if k in UserProfile.model_fields})
    region_parts = (pdict.get("region") or "").split()
    user_sigungu = region_parts[1] if len(region_parts) >= 2 else None

    services = state.get("raw_services", [])
    matched = []
    for s in services:
        if s.get("source") == "행정안전부" and s.get("service_id"):
            try:
                cr = SB.table("welfare_support_conditions").select("*").eq("service_id", s["service_id"]).limit(1).execute()
                cond = cr.data[0] if cr.data else {}
                ok, reason = is_eligible(profile, cond)
                if ok: matched.append({**s, "match_reason": reason})
            except Exception:
                continue
        elif s.get("source") == "지자체" and s.get("service_id"):
            try:
                cr = SB.table("welfare_support_conditions").select("*").eq("service_id", s["service_id"]).limit(1).execute()
                cond = cr.data[0] if cr.data else {}
                ok, reason = is_eligible(profile, cond)
                if ok: matched.append({**s, "match_reason": reason})
            except Exception:
                matched.append({**s, "match_reason": "지자체 (추가확인)"})
        elif s.get("source", "").startswith("Tavily"):
            matched.append({**s, "match_reason": "한시/신규 사업 (담당자 확인)"})
        else:
            matched.append({**s, "match_reason": "지자체/웹 (추가확인)"})

    # 의도 필터
    has_intent_data = any("_intent_score" in m for m in matched)
    if has_intent_data:
        matched_with_intent = [m for m in matched if m.get("_intent_score", 0) > 0]
        if len(matched_with_intent) >= 3:
            matched = matched_with_intent

    # 지자체 ↔ 행정안전부 인터리브
    def _local_rank(s):
        sgg = s.get("region_sigungu")
        if user_sigungu and sgg == user_sigungu: return 0
        if not sgg: return 1
        return 2
    local_list = sorted([m for m in matched if m.get("source") == "지자체"],
                        key=lambda s: (-s.get("_intent_score", 0), _local_rank(s)))
    central_list = sorted([m for m in matched if m.get("source") == "행정안전부"],
                          key=lambda s: -s.get("_intent_score", 0))
    others = [m for m in matched if m.get("source") not in ("지자체", "행정안전부")]
    interleaved = []
    for i in range(max(len(local_list), len(central_list))):
        if i < len(local_list):   interleaved.append(local_list[i])
        if i < len(central_list): interleaved.append(central_list[i])
    matched = interleaved + others

    total = len(services)
    by_source = {}
    for s in matched:
        by_source[s.get("source","?")] = by_source.get(s.get("source","?"), 0) + 1
    src_show = ", ".join([f"{k} {v}건" for k, v in by_source.items()])
    tags = []
    if profile.is_single_household: tags.append("1인가구")
    if profile.is_single_parent: tags.append("한부모")
    if profile.has_disability: tags.append("장애")
    if profile.is_rural: tags.append("농어촌")
    profile_summary = f"{profile.age}세 / {profile.region or '?'} / 월소득 {profile.monthly_income or '?'}원 / {'·'.join(tags) if tags else '-'}"

    msg = f"✅ 자격 매칭: {len(matched)}/{total}건 통과 ({src_show})\n   ↳ 프로필: {profile_summary}"
    return {"eligible_benefits": matched, "messages": [AIMessage(content=msg)]}

# ============================================================
# 노드 3: present_results
# ============================================================
PRESENT_PROMPT = """당신은 어르신·주민에게 복지 정보를 친절하게 안내하는 동네 상담사입니다.

출력 형식:
1) 인삿말 한 줄 (어르신/주민 호칭)
2) 의도 기반 안내 한 줄 (예: "출산하신 한부모 가정에 맞춰 다음 복지를 추렸어요")
3) 받을 수 있는 복지 3~8개. 입력 순서 유지(의도 매칭 점수 높은 순). 각 항목:
   - **이름** (소관기관, 출처: 행정안전부/지자체)
   - 무엇을 받을 수 있는지 한 줄
   - 어떻게 신청하는지 한 줄
   - 💡 매칭 사유
4) 마무리: "혹시 마음에 안 드시거나, 더 알고 싶은 복지가 있으면 편하게 말씀해 주세요. 담당자에게 직접 전달해드릴 수 있습니다."

중요:
- 의도와 명백히 무관한 항목은 빼세요 (출산 의도인데 치매·보훈 등).
- 지자체와 행정안전부를 골고루.
- 매칭 사유는 반드시 표시. 짧고 따뜻하게."""

def present_results_node(state: WelfareState) -> dict:
    print("📍 [3/3] present_results 진입")
    benefits = state.get("eligible_benefits", [])
    profile = state.get("user_profile") or {}
    if not benefits:
        msg = "죄송합니다, 조건에 맞는 복지를 찾지 못했어요. 다른 조건으로 다시 시도해보시거나 담당자에게 상담 요청을 보내드릴까요?"
        return {"presented": True, "messages": [AIMessage(content=msg)]}

    profile_txt = (
        f"{profile.get('age','?')}세 / {profile.get('region','?')} / "
        f"월소득 {profile.get('monthly_income','?')}원 / "
        f"가구형태: {'1인가구' if profile.get('is_single_household') else '복수가구'}"
        f"{', 한부모' if profile.get('is_single_parent') else ''}"
        f"{', 장애' if profile.get('has_disability') else ''}"
    )

    def _item_text(b):
        aug = b.get("tavily_augment") or {}
        aug_line = ""
        if aug:
            aug_line = f"\n  웹검색 보충: {aug.get('snippet','')[:200]} (출처: {aug.get('url','')})"
        return (
            f"- 이름: {b.get('service_name','')}\n"
            f"  소관: {b.get('agency_name','?')}\n"
            f"  출처: {b.get('source','?')}\n"
            f"  지역: {b.get('region_sido','')}/{b.get('region_sigungu','-')}\n"
            f"  내용: {(b.get('support_content') or '')[:150]}\n"
            f"  신청: {(b.get('apply_method') or '')[:100]}\n"
            f"  매칭사유: {b.get('match_reason','-')}"
            + aug_line
        )
    items_txt = "\n".join([_item_text(b) for b in benefits[:12]])

    intent_hint = ""
    for m in reversed(state["messages"]):
        if isinstance(m, AIMessage) and "의도:" in m.content:
            for line in m.content.split("\n"):
                if "의도:" in line:
                    intent_hint = line.split("의도:", 1)[1].strip()
                    break
            break

    user_input = (
        f"사용자 의도 요약: {intent_hint or '(미상)'}\n"
        f"어르신 프로필: {profile_txt}\n\n"
        f"매칭된 복지 목록 (의도 점수 높은 순):\n{items_txt}"
    )
    out = llm.invoke([SystemMessage(content=PRESENT_PROMPT), HumanMessage(content=user_input)])
    return {"presented": True, "messages": [AIMessage(content=out.content)]}

# ============================================================
# 노드 4: feedback_analyzer
# ============================================================
class FeedbackVerdict(BaseModel):
    satisfaction: Literal["satisfied", "unsatisfied", "needs_more"] = Field(
        description="satisfied=만족, needs_more=추가요청, unsatisfied=부적절"
    )
    summary: str = Field(description="피드백 1-2문장 요약")
    requested_topics: List[str] = Field(default_factory=list)

FB_PROMPT = """피드백 분류:
- satisfied: 만족·고맙다
- needs_more: 다른 정보 요청
- unsatisfied: 상황에 안 맞다·불만"""

def feedback_analyzer_node(state: WelfareState) -> dict:
    print("📍 [feedback] feedback_analyzer 진입")
    last_user = next((m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None)
    if not last_user:
        return {"satisfaction": "satisfied"}
    structured = classifier_llm.with_structured_output(FeedbackVerdict)
    v = structured.invoke([SystemMessage(content=FB_PROMPT), HumanMessage(content=last_user.content)])
    summary = v.summary + (f" [추가요청: {', '.join(v.requested_topics)}]" if v.requested_topics else "")
    return {"satisfaction": v.satisfaction, "feedback_summary": summary,
            "messages": [AIMessage(content=f"[분류] {v.satisfaction} / {v.summary}")]}

# ============================================================
# 노드 5: escalate_email
# ============================================================
def escalate_email_node(state: WelfareState) -> dict:
    print("📍 [escalate] escalate_email 진입")
    profile = state.get("user_profile") or {}
    summary = state.get("feedback_summary", "")
    benefits = state.get("eligible_benefits", [])
    benefits_txt = "\n".join([f"  - {b.get('service_name')} ({b.get('agency_name','')})" for b in benefits[:5]]) or "  - (해당 없음)"
    subject = f"[복지마을 방송국] 추가 상담 요청 - {profile.get('region','지역 미상')} {profile.get('age','?')}세"
    body = f"""안녕하세요. 복지마을 방송국 AI 시스템에서 자동 전달드리는 민원입니다.

▣ 신청인 정보
   - 연령: {profile.get('age','?')}세
   - 거주: {profile.get('region','?')}
   - 월소득: {profile.get('monthly_income','?')}원
   - 장애여부: {'있음' if profile.get('has_disability') else '없음'}
   - 가구형태: {'1인가구' if profile.get('is_single_household') else '-'} {'한부모/조손' if profile.get('is_single_parent') else ''}

▣ AI가 안내한 복지
{benefits_txt}

▣ 사용자 피드백 / 추가 요청
   {summary}

▣ 요청사항
   위 내용을 바탕으로 추가 상담 또는 직권 안내를 부탁드립니다.

— 복지마을 방송국 AI 시스템
"""
    result = send_complaint_email(subject, body)
    return {"email_sent": True,
            "messages": [AIMessage(content=f"📧 민원 담당자에게 전달 완료 ({result['status']})\n→ {result['to']}")]}

# ============================================================
# 노드 6: easy_translate (broadcast)
# ============================================================
EASY_PROMPT = """행정 공지를 시골 어르신도 알아듣게 풀어주세요.
규칙: 한자어/외래어 줄이기 / 짧은 문장 / 따뜻한 어투 / 신청법 명확
dialect=='chungcheong' 충청도, 'jeolla' 전라도, 그 외 표준어"""

def easy_translate_node(state: WelfareState) -> dict:
    print("📍 [방송 1/2] easy_translate 진입")
    last_user = next((m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None)
    src = last_user.content if last_user else ""
    dialect = (state.get("user_profile") or {}).get("dialect", "standard")
    out = llm.invoke([SystemMessage(content=EASY_PROMPT + f"\ndialect: {dialect}"),
                      HumanMessage(content=f"다음 공지를 변환:\n\n{src}")])
    return {"easy_text": out.content,
            "messages": [AIMessage(content=f"📣 쉬운 말:\n{out.content}")]}

# ============================================================
# 노드 7: broadcast_script (broadcast)
# ============================================================
BROADCAST_PROMPT = """마을 스피커 방송 멘트 구조:
1) 인삿말 2) 핵심 안내 3) 대상자 4) 신청 방법 5) 마무리
분량: 30초 (한국어 90-130자). 따뜻하고 천천히."""

def broadcast_script_node(state: WelfareState) -> dict:
    print("📍 [방송 2/2] broadcast_script 진입")
    src = state.get("easy_text", "")
    out = llm.invoke([SystemMessage(content=BROADCAST_PROMPT), HumanMessage(content=f"원문:\n{src}")])
    return {"broadcast_text": out.content,
            "messages": [AIMessage(content=f"📻 방송 스크립트:\n{out.content}")]}

# ============================================================
# Dispatcher + Graph
# ============================================================
def dispatcher(state: WelfareState) -> str:
    mode = state.get("mode", "personal")
    msgs = state.get("messages", [])
    if mode == "broadcast":
        if not state.get("easy_text"):      return "easy_translate"
        if not state.get("broadcast_text"): return "broadcast_script"
        return END
    if not state.get("raw_services"):      return "welfare_search"
    if not state.get("eligible_benefits"): return "eligibility_check"
    if not state.get("presented"):         return "present_results"
    last_human = next((m for m in reversed(msgs) if isinstance(m, HumanMessage)), None)
    last_idx = msgs.index(last_human) if last_human in msgs else -1
    last_ai_idx = max((i for i, m in enumerate(msgs) if isinstance(m, AIMessage)), default=-1)
    new_feedback = last_idx > last_ai_idx
    sat = state.get("satisfaction", "")
    if new_feedback and not sat:
        return "feedback_analyzer"
    if sat in ("unsatisfied", "needs_more") and not state.get("email_sent"):
        return "escalate_email"
    return END

builder = StateGraph(WelfareState)
builder.add_node("welfare_search",    welfare_search_node)
builder.add_node("eligibility_check", eligibility_node)
builder.add_node("present_results",   present_results_node)
builder.add_node("feedback_analyzer", feedback_analyzer_node)
builder.add_node("escalate_email",    escalate_email_node)
builder.add_node("easy_translate",    easy_translate_node)
builder.add_node("broadcast_script",  broadcast_script_node)

route_map = {
    "welfare_search": "welfare_search",
    "eligibility_check": "eligibility_check",
    "present_results": "present_results",
    "feedback_analyzer": "feedback_analyzer",
    "escalate_email": "escalate_email",
    "easy_translate": "easy_translate",
    "broadcast_script": "broadcast_script",
    END: END,
}
builder.add_conditional_edges(START, dispatcher, route_map)
for n in list(route_map.keys()):
    if n != END:
        builder.add_conditional_edges(n, dispatcher, route_map)

graph = builder.compile(checkpointer=InMemorySaver())

def cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}
