-- ============================================================================
-- Migration 0002 DOWN — återställer rättigheterna till 0001:s läge.
-- Kör i Supabase SQL-editorn med service-rollen.
-- ============================================================================

-- Återställ anon/authenticated till 0001:s (för breda) läsning.
grant select on tp_revisions        to anon, authenticated;
grant select on analyst_house_alias to anon, authenticated;
grant select on tp_revisions_enriched to anon, authenticated;

-- Ta bort service_role:s vy-SELECT (0001 gav den aldrig).
revoke select on tp_revisions_enriched, tp_acceleration_current from service_role;

-- Funktionen tillbaka till SECURITY INVOKER + nollställ pinnad search_path.
alter function get_tp_acceleration(text, integer, integer) security invoker;
alter function get_tp_acceleration(text, integer, integer) reset search_path;

notify pgrst, 'reload schema';
