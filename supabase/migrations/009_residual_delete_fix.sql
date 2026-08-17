-- 009_residual_delete_fix.sql — residual 보호 트리거가 cascade 삭제까지 막던 문제 수정
-- 배경: 004의 protect_residual은 노트/갈래 삭제의 cascade에도 발화해 삭제 자체가 불가능했다.
-- 부모 갈래가 이미 지워지는 중(cascade)이면 허용하고, 갈래가 살아 있는데
-- residual만 직접 지우는 것은 계속 차단한다.

begin;

create or replace function protect_residual()
returns trigger language plpgsql as $$
begin
  if old.is_residual
     and exists (select 1 from galae where id = old.galae_id) then
    raise exception 'residual scenario cannot be deleted';
  end if;
  return old;
end $$;

commit;
