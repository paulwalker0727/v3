CREATE OR REPLACE FUNCTION public.cp_binding_uid()
 RETURNS text
 LANGUAGE sql
 STABLE
AS $function$ SELECT NULLIF(current_setting('cp.binding_uid', true), '') $function$
;
CREATE OR REPLACE FUNCTION public.cp_cem_ok(cem text)
 RETURNS boolean
 LANGUAGE sql
 STABLE
AS $function$ SELECT cp_platform() OR COALESCE(current_setting('cp.cems', true), '') = '*'
          OR (cem IS NOT NULL AND cem = ANY (string_to_array(NULLIF(current_setting('cp.cems', true), ''), ','))) $function$
;
CREATE OR REPLACE FUNCTION public.cp_cemetery_org(cem text)
 RETURNS text
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public', 'pg_catalog'
AS $function$ SELECT CASE WHEN cp_platform() OR c.organization_uid = cp_org() THEN c.organization_uid ELSE '00000000-0000-0000-0000-000000000000' END
   FROM cp_cemeteries c WHERE c.uid = cem $function$
;
CREATE OR REPLACE FUNCTION public.cp_code_hash()
 RETURNS text
 LANGUAGE sql
 STABLE
AS $function$ SELECT NULLIF(current_setting('cp.code_hash', true), '') $function$
;
CREATE OR REPLACE FUNCTION public.cp_fill_org()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.organization_uid IS NULL THEN
        IF TG_TABLE_NAME = 'cp_binding_issuers' THEN
            SELECT b.organization_uid INTO NEW.organization_uid FROM cp_bindings b WHERE b.binding_uid = NEW.binding_uid;
        ELSE
            SELECT c.organization_uid INTO NEW.organization_uid FROM cp_cemeteries c WHERE c.uid = NEW.cemetery_uid;
        END IF;
    END IF;
    RETURN NEW;
END $function$
;
CREATE OR REPLACE FUNCTION public.cp_key_hash()
 RETURNS text
 LANGUAGE sql
 STABLE
AS $function$ SELECT NULLIF(current_setting('cp.key_hash', true), '') $function$
;
CREATE OR REPLACE FUNCTION public.cp_org()
 RETURNS text
 LANGUAGE sql
 STABLE
AS $function$ SELECT NULLIF(current_setting('cp.org', true), '') $function$
;
CREATE OR REPLACE FUNCTION public.cp_org_ok(org text)
 RETURNS boolean
 LANGUAGE sql
 STABLE
AS $function$ SELECT cp_platform() OR (org IS NOT NULL AND org = cp_org()) $function$
;
CREATE OR REPLACE FUNCTION public.cp_platform()
 RETURNS boolean
 LANGUAGE sql
 STABLE
AS $function$ SELECT COALESCE(current_setting('cp.platform', true), '') = '1' $function$
;
