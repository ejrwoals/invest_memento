-- 001_foundation.sql — 확장, 프로필, 가입 트리거
-- 근거: docs/dev/01-db-schema.md §3.1
-- 적용: Supabase SQL editor에 전체 붙여넣기 → Run

begin;

create extension if not exists moddatetime with schema extensions;

-- ── profiles ──────────────────────────────────────────────────────────────
create table profiles (
  user_id              uuid primary key references auth.users on delete cascade,
  display_name         text,
  disclaimer_agreed_at timestamptz,          -- 온보딩 면책 동의 시각. null이면 미동의
  reminder_channel     text not null default 'email'
                       check (reminder_channel in ('email')),
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);

alter table profiles enable row level security;

create policy profiles_owner on profiles
  for all to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

create trigger profiles_updated_at
  before update on profiles
  for each row execute procedure extensions.moddatetime(updated_at);

-- ── 가입 시 프로필 자동 생성 (Google 로그인 포함) ─────────────────────────
create or replace function public.handle_new_user()
returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (user_id, display_name)
  values (
    new.id,
    coalesce(new.raw_user_meta_data ->> 'full_name',
             new.raw_user_meta_data ->> 'name',
             split_part(new.email, '@', 1))
  )
  on conflict (user_id) do nothing;
  return new;
end $$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

commit;
