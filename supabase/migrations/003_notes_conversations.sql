-- 003_notes_conversations.sql — 노트, 원본 대화(불변), 본문 블록, 첨부
-- 근거: docs/dev/01-db-schema.md §3.3, §4.2
-- 선행: 001, 002

begin;

-- ── notes ─────────────────────────────────────────────────────────────────
create table notes (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users on delete cascade,
  target_type    text not null check (target_type in ('ticker','asset','theme')),
  target_symbol  text references instruments(symbol),   -- ticker일 때만
  target_name    text not null,
  thesis_summary text not null,              -- 한 문장 (NO_THESIS는 blocking)
  thesis_detail  text,
  color          text not null,              -- 홈 타임라인 식별색
  archived_at    timestamptz,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
-- is_complete 컬럼은 없다 — 갈래 판단 시점 유무에서 파생, API가 계산해 싣는다.

create index notes_user_idx on notes (user_id, archived_at);

create trigger notes_updated_at
  before update on notes
  for each row execute procedure extensions.moddatetime(updated_at);

-- ── conversations ─────────────────────────────────────────────────────────
-- 대화는 노트보다 먼저 태어난다(draft 재개). 노트가 삭제돼도 대화는 남는다(set null).
create table conversations (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users on delete cascade,
  note_id    uuid unique references notes on delete set null,
  status     text not null default 'draft'
             check (status in ('draft','attached','abandoned')),
  draft_note jsonb,                          -- 작성 중 실시간 패널 상태 (UX §3.2)
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger conversations_updated_at
  before update on conversations
  for each row execute procedure extensions.moddatetime(updated_at);

create table conversation_messages (
  id              uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references conversations on delete cascade,
  seq             int  not null,
  role            text not null check (role in ('user','assistant')),
  content         text not null,
  created_at      timestamptz not null default now(),
  unique (conversation_id, seq)
);

-- 원본 대화 불변 (P2): UPDATE는 절대 불가. DELETE는 계정 삭제 등 전체 퍼지 시에만,
-- 세션 GUC(app.allow_conversation_purge = 'on')를 명시적으로 켠 트랜잭션에서만 허용.
create or replace function conversation_messages_guard()
returns trigger language plpgsql as $$
begin
  if tg_op = 'UPDATE' then
    raise exception 'conversation_messages is immutable';
  end if;
  if coalesce(current_setting('app.allow_conversation_purge', true), '') <> 'on' then
    raise exception 'conversation_messages delete requires app.allow_conversation_purge=on';
  end if;
  return old;
end $$;

create trigger conversation_messages_immutable
  before update or delete on conversation_messages
  for each row execute function conversation_messages_guard();

-- ── content_blocks ────────────────────────────────────────────────────────
create table content_blocks (
  id          uuid primary key default gen_random_uuid(),
  note_id     uuid not null references notes on delete cascade,
  section     text not null check (section in
              ('thesis','thesis_quote','scenario','premise_intro','free')),
  position    int  not null default 0,
  content     text not null,
  authorship  text not null check (authorship in ('ai','user')),  -- user만 [사용자] 표기
  quoted_from uuid references conversation_messages,
  derived     boolean not null default false,
  created_at  timestamptz not null default now()
);

create index content_blocks_note_idx on content_blocks (note_id, position);

-- ── sources (사용자 첨부) ─────────────────────────────────────────────────
create table sources (
  id           uuid primary key default gen_random_uuid(),
  note_id      uuid not null references notes on delete cascade,
  type         text not null check (type in ('link','text','file','image')),
  url          text,
  content      text,
  storage_path text,
  created_at   timestamptz not null default now()
);

-- ── RLS ───────────────────────────────────────────────────────────────────
alter table notes enable row level security;
alter table conversations enable row level security;
alter table conversation_messages enable row level security;
alter table content_blocks enable row level security;
alter table sources enable row level security;

create policy notes_owner on notes
  for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

create policy conversations_owner on conversations
  for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

create policy conversation_messages_owner on conversation_messages
  for all to authenticated
  using (exists (select 1 from conversations c
                 where c.id = conversation_id and c.user_id = auth.uid()))
  with check (exists (select 1 from conversations c
                      where c.id = conversation_id and c.user_id = auth.uid()));

create policy content_blocks_owner on content_blocks
  for all to authenticated
  using (exists (select 1 from notes n
                 where n.id = note_id and n.user_id = auth.uid()))
  with check (exists (select 1 from notes n
                      where n.id = note_id and n.user_id = auth.uid()));

create policy sources_owner on sources
  for all to authenticated
  using (exists (select 1 from notes n
                 where n.id = note_id and n.user_id = auth.uid()))
  with check (exists (select 1 from notes n
                      where n.id = note_id and n.user_id = auth.uid()));

commit;
