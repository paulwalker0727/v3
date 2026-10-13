-- 017: retrage membership-ul TEMPORAR pe cimitir_storage_cleanup pe care 016 l-a cerut rolului care a rulat migrarea (necesar doar pentru ALTER FUNCTION ... OWNER TO).
-- După 016, ownership-ul celor 5 funcții cleanup e la cimitir_storage_cleanup; rolul migrației nu mai trebuie să poată face SET ROLE / moșteni drepturile lui.
-- Scop unic: nu modifică funcții, owneri, privilegii, RLS, Storage sau date. Rolul care execută migrarea e identificat cu current_user (fără nume hard-codate).
-- Se retrage DOAR grant-ul făcut de acest rol către el însuși (cel creat de 016); grant-urile făcute de platformă (ex. Supabase: ADMIN-only de la supabase_admin,
-- fără INHERIT/SET) rămân și nu dau drepturi de folosire a rolului. Dacă 016 nu a avut nevoie de grant (superuser) nu se întâmplă nimic. Idempotent.
-- Notă pentru migrații viitoare care înlocuiesc funcțiile cleanup: repetă tiparul 016 (GRANT tranzitoriu -> lucru -> REVOKE).
DO $$
DECLARE
    v_role oid := (SELECT oid FROM pg_roles WHERE rolname = 'cimitir_storage_cleanup');
    v_me   oid := (SELECT oid FROM pg_roles WHERE rolname = current_user);
BEGIN
    IF v_role IS NOT NULL AND v_me IS NOT NULL AND v_role <> v_me
       AND EXISTS (SELECT 1 FROM pg_auth_members WHERE roleid = v_role AND member = v_me AND grantor = v_me) THEN
        EXECUTE 'REVOKE cimitir_storage_cleanup FROM CURRENT_USER';
    END IF;
END $$;
