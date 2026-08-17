-- 011_in_app_channel.sql — 이메일 리마인드 제거: 알림 채널은 인앱뿐이다
-- 근거: M5 범위 결정 — Resend 연동 없음. 리마인드는 홈 피드 + notifications 행 전용.
-- 선행: 006

begin;

-- notifications.channel: 'email' → 'in_app'
alter table notifications drop constraint if exists notifications_channel_check;
alter table notifications alter column channel set default 'in_app';
update notifications set channel = 'in_app' where channel <> 'in_app';
alter table notifications add constraint notifications_channel_check
  check (channel in ('in_app'));

-- profiles.reminder_channel: 'email' → 'in_app'
alter table profiles drop constraint if exists profiles_reminder_channel_check;
alter table profiles alter column reminder_channel set default 'in_app';
update profiles set reminder_channel = 'in_app' where reminder_channel <> 'in_app';
alter table profiles add constraint profiles_reminder_channel_check
  check (reminder_channel in ('in_app'));

commit;
