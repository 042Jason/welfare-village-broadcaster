-- ============================================================
-- 복지마을 방송국 - Supabase 스키마
-- 사용법: Supabase Dashboard -> SQL Editor -> 전체 붙여넣기 -> RUN
-- ============================================================

-- 부분 일치 검색을 위한 trigram 확장 (서비스명 LIKE 검색용)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- 1. welfare_services : 복지 서비스 마스터
--    - 행정안전부(공공서비스 혜택) + 한국사회보장정보원(지자체복지) 통합
-- ============================================================
CREATE TABLE IF NOT EXISTS welfare_services (
    id                  BIGSERIAL PRIMARY KEY,
    source              VARCHAR(20)  NOT NULL,        -- '행정안전부' | '지자체'
    service_id          VARCHAR(100) NOT NULL UNIQUE, -- 공공서비스ID / 지자체 servId

    service_name        TEXT         NOT NULL,
    service_summary     TEXT,                         -- 서비스목적요약
    agency_name         TEXT,                         -- 소관기관명
    agency_type         VARCHAR(50),                  -- 소관기관유형
    department          TEXT,                         -- 부서명
    support_type        VARCHAR(50),                  -- 지원유형 (현금/현물/서비스 등)

    user_type           TEXT,                         -- 사용자구분 (노인/장애인/...)
    service_field       TEXT,                         -- 서비스분야

    target_description  TEXT,                         -- 지원대상 설명
    selection_criteria  TEXT,                         -- 선정기준
    support_content     TEXT,                         -- 지원내용
    apply_method        TEXT,                         -- 신청방법
    apply_deadline      TEXT,                         -- 신청기한
    receiving_agency    TEXT,                         -- 접수기관
    contact             TEXT,                         -- 전화문의
    detail_url          TEXT,                         -- 상세조회URL

    -- 지자체 전용 필드
    region_sido         VARCHAR(50),                  -- 시도명
    region_sigungu      VARCHAR(50),                  -- 시군구명
    life_stages         TEXT[],                       -- 생애주기 array
    interest_themes     TEXT[],                       -- 관심주제 array

    raw_data            JSONB,                        -- 원본 응답 (디버깅/재처리용)
    fetched_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_services_source       ON welfare_services(source);
CREATE INDEX IF NOT EXISTS idx_services_region_sido  ON welfare_services(region_sido);
CREATE INDEX IF NOT EXISTS idx_services_user_type    ON welfare_services(user_type);
CREATE INDEX IF NOT EXISTS idx_services_field        ON welfare_services(service_field);
-- 서비스명 부분 검색 (LIKE / ILIKE) 가속
CREATE INDEX IF NOT EXISTS idx_services_name_trgm    ON welfare_services USING gin (service_name gin_trgm_ops);

-- updated_at 자동 갱신 트리거
CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_services_touch ON welfare_services;
CREATE TRIGGER trg_services_touch
    BEFORE UPDATE ON welfare_services
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ============================================================
-- 2. welfare_support_conditions : 자격 조건 (행정안전부 supportConditions)
--    - JA**** 코드를 boolean/int 컬럼으로 정형화
-- ============================================================
CREATE TABLE IF NOT EXISTS welfare_support_conditions (
    id                          BIGSERIAL PRIMARY KEY,
    service_id                  VARCHAR(100) NOT NULL UNIQUE
                                REFERENCES welfare_services(service_id) ON DELETE CASCADE,

    -- 연령 (JA0110, JA0111)
    age_start                   INT,
    age_end                     INT,

    -- 성별 (JA0101, JA0102)
    male_eligible               BOOLEAN DEFAULT TRUE,
    female_eligible             BOOLEAN DEFAULT TRUE,

    -- 중위소득 구간 (JA0201~JA0205)
    income_band_50              BOOLEAN DEFAULT FALSE,
    income_band_75              BOOLEAN DEFAULT FALSE,
    income_band_100             BOOLEAN DEFAULT FALSE,
    income_band_200             BOOLEAN DEFAULT FALSE,
    income_band_over200         BOOLEAN DEFAULT FALSE,

    -- 가구 형태 (JA0401~JA0414)
    multi_cultural              BOOLEAN DEFAULT FALSE,  -- JA0401 다문화
    north_korean_defector       BOOLEAN DEFAULT FALSE,  -- JA0402 북한이탈
    single_parent               BOOLEAN DEFAULT FALSE,  -- JA0403 한부모/조손
    single_household            BOOLEAN DEFAULT FALSE,  -- JA0404 1인가구
    multi_child                 BOOLEAN DEFAULT FALSE,  -- JA0411 다자녀
    no_house                    BOOLEAN DEFAULT FALSE,  -- JA0412 무주택

    -- 개인 특성 (JA0301~, JA0328~)
    expecting_parent            BOOLEAN DEFAULT FALSE,  -- JA0301 예비부모/난임
    pregnant                    BOOLEAN DEFAULT FALSE,  -- JA0302 임산부
    postpartum                  BOOLEAN DEFAULT FALSE,  -- JA0303 출산/입양
    farmer                      BOOLEAN DEFAULT FALSE,  -- JA0313 농업인
    fisher                      BOOLEAN DEFAULT FALSE,  -- JA0314 어업인
    livestock                   BOOLEAN DEFAULT FALSE,  -- JA0315 축산업인
    forester                    BOOLEAN DEFAULT FALSE,  -- JA0316 임업인
    elementary                  BOOLEAN DEFAULT FALSE,  -- JA0317 초등학생
    middle_school               BOOLEAN DEFAULT FALSE,  -- JA0318 중학생
    high_school                 BOOLEAN DEFAULT FALSE,  -- JA0319 고등학생
    university                  BOOLEAN DEFAULT FALSE,  -- JA0320 대학생/대학원생
    employed                    BOOLEAN DEFAULT FALSE,  -- JA0326 근로자/직장인
    unemployed                  BOOLEAN DEFAULT FALSE,  -- JA0327 구직자/실업자
    disabled                    BOOLEAN DEFAULT FALSE,  -- JA0328 장애인
    veteran                     BOOLEAN DEFAULT FALSE,  -- JA0329 국가보훈
    illness                     BOOLEAN DEFAULT FALSE,  -- JA0330 질병/질환자

    raw_codes                   JSONB,                  -- 원본 JA**** 전체
    fetched_at                  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conditions_age           ON welfare_support_conditions(age_start, age_end);
CREATE INDEX IF NOT EXISTS idx_conditions_disabled      ON welfare_support_conditions(disabled) WHERE disabled = true;
CREATE INDEX IF NOT EXISTS idx_conditions_single_parent ON welfare_support_conditions(single_parent) WHERE single_parent = true;

-- ============================================================
-- 3. (선택) 적재 작업 로그 - 누적 모니터링용
-- ============================================================
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id              BIGSERIAL PRIMARY KEY,
    source          VARCHAR(20) NOT NULL,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    fetched_count   INT,
    upserted_count  INT,
    error_count     INT,
    note            TEXT
);

-- ============================================================
-- 4. RLS (Row Level Security) - 익명 키로 읽기만 허용
--    에이전트 노트북은 anon key 사용
--    적재 노트북은 service_role key 사용 (RLS 우회)
-- ============================================================
ALTER TABLE welfare_services ENABLE ROW LEVEL SECURITY;
ALTER TABLE welfare_support_conditions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow anon read services" ON welfare_services;
CREATE POLICY "Allow anon read services"
    ON welfare_services FOR SELECT
    USING (true);

DROP POLICY IF EXISTS "Allow anon read conditions" ON welfare_support_conditions;
CREATE POLICY "Allow anon read conditions"
    ON welfare_support_conditions FOR SELECT
    USING (true);

-- ============================================================
-- 완료 메시지
-- ============================================================
DO $$ BEGIN
    RAISE NOTICE '✅ welfare_services / welfare_support_conditions / ingestion_runs 생성 완료';
END $$;
