-- 010_series_seed.sql — series_catalog 초기 시드
-- 근거: docs/dev/05-series-service.md §3.1 (시드 기준: "Thesis Builder 가 후보로 올릴 만한 계열")
-- 개별 주식은 시드하지 않는다 — instruments 등록 시 kind='equity' 로 동적 upsert 한다.
-- 선행: 002 (series_catalog)

begin;

insert into series_catalog (provider, code, label, kind, unit, has_intraday, search_keywords) values
  -- ── 지수 (kis) ──────────────────────────────────────────────────────────
  -- TODO: KIS 일봉 API 의 실제 지수 코드 확인 필요 (05 §10 착수 시 확인 목록).
  --       국내는 업종 지수 코드(코스피 0001·코스닥 1001), 해외는 KIS 해외지수 심볼을
  --       가정했다. 개발 중에는 yfinance 어댑터가 이 코드를 ^KS11 등으로 매핑한다.
  ('kis',  '0001',    '코스피',                    'index', 'pt',    true,
   array['코스피','KOSPI','국내 증시','한국 주식시장']),
  ('kis',  '1001',    '코스닥',                    'index', 'pt',    true,
   array['코스닥','KOSDAQ']),
  ('kis',  'SPX',     'S&P500',                    'index', 'pt',    true,
   array['S&P500','에스앤피','SPX','미국 증시','미국 주식시장']),
  ('kis',  'COMP',    '나스닥',                    'index', 'pt',    true,
   array['나스닥','NASDAQ','기술주']),

  -- ── 거시 (fred) — 월·분기 계열도 매일 조회한다 (05 §2.2) ────────────────
  ('fred', 'DFF',       '미국 기준금리(실효 연방기금금리)', 'macro', '%',     false,
   array['미국 기준금리','연준','Fed','연방기금금리','금리']),
  ('fred', 'CPIAUCSL',  '미국 소비자물가지수(CPI)',  'macro', 'index', false,
   array['미국 CPI','미국 물가','인플레이션','소비자물가']),
  ('fred', 'UNRATE',    '미국 실업률',               'macro', '%',     false,
   array['미국 실업률','고용','실업']),
  ('fred', 'DGS10',     '미국 10년물 국채금리',      'macro', '%',     false,
   array['미 10년물','국채금리','장기금리','10년물']),
  ('fred', 'DCOILWTICO','WTI 유가(현물)',            'macro', 'USD',   false,
   array['WTI','유가','원유','국제유가']),

  -- ── 거시·환율 (ecos) — code 는 통계표코드/주기/항목코드 (05 §2.3) ───────
  ('ecos', '722Y001/D/0101000', '한국 기준금리',     'macro', '%',     false,
   array['한국 기준금리','한은','한국은행','기준금리','금통위']),
  -- TODO: ECOS 소비자물가지수 총지수의 정확한 항목코드 확인 필요 (901Y009 가정)
  ('ecos', '901Y009/M/0',       '한국 소비자물가지수 (코드 확인 필요)', 'macro', 'index', false,
   array['한국 CPI','한국 물가','소비자물가지수']),
  -- TODO: ECOS 원/달러 매매기준율 일별 계열의 정확한 통계코드 확인 필요 (05 §10,
  --       731Y001/D/0000001 가정). PnlSnapshot·환차손익 계산의 원천이므로 착수 시 확정.
  ('ecos', '731Y001/D/0000001', '원/달러 매매기준율 (코드 확인 필요)', 'fx', 'KRW',  false,
   array['원달러','환율','원/달러','매매기준율','달러'])
on conflict (provider, code) do nothing;

commit;
