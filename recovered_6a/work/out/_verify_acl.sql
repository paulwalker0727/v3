--
-- PostgreSQL database dump
--

\restrict beaYEmnHs9LiiHo5w1RwR0mkkh8ScBT4MefzJEpWRenhUKaSVZbffLp2Pvtms4P

-- Dumped from database version 15.19 (Debian 15.19-0+deb12u1)
-- Dumped by pg_dump version 15.19 (Debian 15.19-0+deb12u1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: app_cemetery_uids(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.app_cemetery_uids() RETURNS uuid[]
    LANGUAGE sql STABLE
    AS $$ SELECT CASE WHEN COALESCE(current_setting('app.cemetery_uids', true), '') = '' THEN ARRAY[]::uuid[]
               ELSE string_to_array(current_setting('app.cemetery_uids', true), ',')::uuid[] END $$;


--
-- Name: app_default_cemetery_uid(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.app_default_cemetery_uid() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT CASE WHEN cardinality(app_cemetery_uids()) = 1 THEN (app_cemetery_uids())[1] END $$;


--
-- Name: app_org_uid(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.app_org_uid() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('app.org_uid', true), '')::uuid $$;


--
-- Name: cimitir_concat_step(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cimitir_concat_step(state text, v text) RETURNS text
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT CASE WHEN state IS NULL THEN v WHEN v IS NULL THEN state ELSE state || ',' || v END
$$;


--
-- Name: cimitir_media_tombstone(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cimitir_media_tombstone() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO sync_tombstones (entity, uid, version, change_seq, organization_uid, cemetery_uid)
    VALUES ('media_objects', OLD.id, 1, nextval('sync_change_seq'), OLD.organization_uid, OLD.cemetery_uid)
    ON CONFLICT (entity, uid) DO UPDATE SET change_seq = EXCLUDED.change_seq, deleted_at = now(),
        organization_uid = EXCLUDED.organization_uid, cemetery_uid = EXCLUDED.cemetery_uid;
    RETURN OLD;
END;
$$;


--
-- Name: cimitir_media_touch(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cimitir_media_touch() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.change_seq = nextval('sync_change_seq');
    RETURN NEW;
END;
$$;


--
-- Name: cimitir_reset_sequences(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cimitir_reset_sequences() RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE r record; n integer := 0;
BEGIN
    FOR r IN SELECT c.table_name FROM information_schema.columns c
             WHERE c.table_schema = 'public' AND c.column_name = 'id' AND c.is_identity = 'YES'
    LOOP
        EXECUTE format(
            'SELECT setval(pg_get_serial_sequence(%L, ''id''), COALESCE((SELECT MAX(id) FROM %I), 1), (SELECT MAX(id) FROM %I) IS NOT NULL)',
            r.table_name, r.table_name, r.table_name);
        n := n + 1;
    END LOOP;
    RETURN n;
END;
$$;


--
-- Name: cimitir_set_modified_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cimitir_set_modified_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.modified_at = now();
    RETURN NEW;
END;
$$;


--
-- Name: cimitir_sync_tombstone(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cimitir_sync_tombstone() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO sync_tombstones (entity, uid, version, change_seq, organization_uid, cemetery_uid)
    VALUES (TG_TABLE_NAME, OLD.uid, COALESCE(OLD.version, 0) + 1, nextval('sync_change_seq'), OLD.organization_uid, OLD.cemetery_uid)
    ON CONFLICT (entity, uid) DO UPDATE SET version = EXCLUDED.version, change_seq = EXCLUDED.change_seq, deleted_at = now(),
        organization_uid = EXCLUDED.organization_uid, cemetery_uid = EXCLUDED.cemetery_uid;
    RETURN OLD;
END;
$$;


--
-- Name: cimitir_sync_touch(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cimitir_sync_touch() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.version = GREATEST(COALESCE(NEW.version, 0), 0) + 1;
    ELSE
        NEW.version = COALESCE(OLD.version, 0) + 1;
    END IF;
    NEW.modified_at = now();
    NEW.change_seq = nextval('sync_change_seq');
    RETURN NEW;
END;
$$;


--
-- Name: cp_binding_uid(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cp_binding_uid() RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('cp.binding_uid', true), '') $$;


--
-- Name: cp_cem_ok(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cp_cem_ok(cem text) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$ SELECT cp_platform() OR COALESCE(current_setting('cp.cems', true), '') = '*'
          OR (cem IS NOT NULL AND cem = ANY (string_to_array(NULLIF(current_setting('cp.cems', true), ''), ','))) $$;


--
-- Name: cp_cemetery_org(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cp_cemetery_org(cem text) RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'pg_catalog'
    AS $$ SELECT CASE WHEN cp_platform() OR c.organization_uid = cp_org() THEN c.organization_uid ELSE '00000000-0000-0000-0000-000000000000' END
   FROM cp_cemeteries c WHERE c.uid = cem $$;


--
-- Name: cp_code_hash(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cp_code_hash() RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('cp.code_hash', true), '') $$;


--
-- Name: cp_fill_org(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cp_fill_org() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.organization_uid IS NULL THEN
        IF TG_TABLE_NAME = 'cp_binding_issuers' THEN
            SELECT b.organization_uid INTO NEW.organization_uid FROM cp_bindings b WHERE b.binding_uid = NEW.binding_uid;
        ELSE
            SELECT c.organization_uid INTO NEW.organization_uid FROM cp_cemeteries c WHERE c.uid = NEW.cemetery_uid;
        END IF;
    END IF;
    RETURN NEW;
END $$;


--
-- Name: cp_key_hash(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cp_key_hash() RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('cp.key_hash', true), '') $$;


--
-- Name: cp_org(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cp_org() RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('cp.org', true), '') $$;


--
-- Name: cp_org_ok(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cp_org_ok(org text) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$ SELECT cp_platform() OR (org IS NOT NULL AND org = cp_org()) $$;


--
-- Name: cp_platform(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cp_platform() RETURNS boolean
    LANGUAGE sql STABLE
    AS $$ SELECT COALESCE(current_setting('cp.platform', true), '') = '1' $$;


--
-- Name: group_concat(text); Type: AGGREGATE; Schema: public; Owner: -
--

CREATE AGGREGATE public.group_concat(text) (
    SFUNC = public.cimitir_concat_step,
    STYPE = text
);


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: ai_scan_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_scan_history (
    id bigint NOT NULL,
    uid text DEFAULT (gen_random_uuid())::text NOT NULL,
    created_at text NOT NULL,
    document_type text NOT NULL,
    filename text,
    file_path text,
    status text DEFAULT 'processed'::text NOT NULL,
    entity_type text,
    entity_id bigint,
    extracted_json text,
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: ai_scan_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ai_scan_history ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.ai_scan_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: amenajari_avize; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.amenajari_avize (
    id bigint NOT NULL,
    uid text DEFAULT (gen_random_uuid())::text NOT NULL,
    cod_loc text NOT NULL,
    nr_aviz text NOT NULL,
    data_aviz text,
    lucrare text,
    created_at text DEFAULT to_char((now() AT TIME ZONE 'utc'::text), 'YYYY-MM-DD HH24:MI:SS'::text),
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    version integer DEFAULT 0 NOT NULL,
    change_seq bigint,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: amenajari_avize_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.amenajari_avize ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.amenajari_avize_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    id bigint NOT NULL,
    ts text,
    actor_type text,
    actor text,
    action text,
    target text,
    detail text,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    organization_uid uuid DEFAULT public.app_org_uid(),
    cemetery_uid uuid
);


--
-- Name: audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.audit_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: cemeteries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cemeteries (
    uid uuid NOT NULL,
    organization_uid uuid NOT NULL,
    name text NOT NULL,
    internal_code text NOT NULL,
    locality text,
    county text,
    address text,
    status text DEFAULT 'ACTIVE'::text NOT NULL,
    map_config jsonb DEFAULT '{}'::jsonb NOT NULL,
    public_slug text,
    public_enabled boolean DEFAULT false NOT NULL,
    branding jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    CONSTRAINT cemeteries_status_check CHECK ((status = ANY (ARRAY['DRAFT'::text, 'ACTIVE'::text, 'ARCHIVED'::text])))
);


--
-- Name: cemetery_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cemetery_permissions (
    member_uid uuid NOT NULL,
    organization_uid uuid NOT NULL,
    cemetery_uid uuid NOT NULL,
    role text DEFAULT 'user'::text NOT NULL,
    rights jsonb DEFAULT '{}'::jsonb NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cereri_concesionare; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cereri_concesionare (
    id bigint NOT NULL,
    uid text DEFAULT (gen_random_uuid())::text NOT NULL,
    created_at text,
    status text DEFAULT 'nou'::text,
    cod_urmarire text,
    cod_loc text,
    nume text,
    prenume text,
    cnp text,
    bi_ci text,
    adresa text,
    telefon text,
    email text,
    grad_rudenie text,
    observatii text,
    concesionar_id bigint,
    contract_id bigint,
    reviewed_at text,
    review_note text,
    sync_version integer DEFAULT 0,
    acked integer DEFAULT 0,
    citizen_id bigint,
    tip text DEFAULT 'concesionare'::text,
    payload text,
    revision integer DEFAULT 0,
    privacy_version text,
    privacy_ack_at text,
    closed_at text,
    public_message text,
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: cereri_concesionare_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.cereri_concesionare ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.cereri_concesionare_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: citizen_resets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.citizen_resets (
    id bigint NOT NULL,
    citizen_id bigint NOT NULL,
    token_hash text NOT NULL,
    expires_at double precision NOT NULL,
    used_at text,
    created_at text,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    organization_uid uuid DEFAULT public.app_org_uid()
);


--
-- Name: citizen_resets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.citizen_resets ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.citizen_resets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: citizens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.citizens (
    id bigint NOT NULL,
    uid text DEFAULT (gen_random_uuid())::text NOT NULL,
    email text NOT NULL,
    password_hash text NOT NULL,
    full_name text,
    created_at text,
    active integer DEFAULT 1,
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL
);


--
-- Name: citizens_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.citizens ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.citizens_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: cloud_sync_docs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cloud_sync_docs (
    tracking_code text NOT NULL,
    doc_id bigint NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cloud_sync_map; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cloud_sync_map (
    tracking_code text NOT NULL,
    cerere_id bigint,
    pushed_status text DEFAULT ''::text,
    imported_at text,
    pushed_version integer DEFAULT 0,
    revision integer DEFAULT 0,
    created_ts timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cloud_sync_outbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cloud_sync_outbox (
    id bigint NOT NULL,
    tracking_code text,
    status text,
    note text,
    created_at text,
    op_id text,
    kind text,
    payload text,
    attempts integer DEFAULT 0,
    last_error text,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    entity text,
    entity_uid text,
    action text,
    base_version integer DEFAULT 0,
    state text DEFAULT 'pending'::text,
    conflict text
);


--
-- Name: cloud_sync_outbox_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.cloud_sync_outbox ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.cloud_sync_outbox_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: concesionari; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.concesionari (
    id bigint NOT NULL,
    uid text DEFAULT (gen_random_uuid())::text NOT NULL,
    nume text,
    prenume text,
    cnp text,
    bi_ci text,
    adresa text,
    telefon text,
    email text,
    observatii text,
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    version integer DEFAULT 0 NOT NULL,
    change_seq bigint,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: concesionari_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.concesionari ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.concesionari_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: contracte; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contracte (
    id bigint NOT NULL,
    uid text DEFAULT (gen_random_uuid())::text NOT NULL,
    numar_contract text,
    data_contract text,
    data_inceput text,
    data_sfarsit text,
    concesionar_id bigint,
    id_loc bigint,
    cod_loc text,
    valabilitate text,
    observatii text,
    extra_data text,
    owner_fullname text,
    owner_fullname_2 text,
    owner_address text,
    owner_id_card_serie_number text,
    owner_cnp text,
    owner_phone text,
    owner_email text,
    cemetery_parcel text,
    cemetery_row text,
    cemetery_tomb text,
    tomb_surface_m2 double precision,
    sector text,
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    version integer DEFAULT 0 NOT NULL,
    change_seq bigint,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: contracte_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.contracte ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.contracte_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: cp_audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_audit_log (
    id bigint NOT NULL,
    ts double precision,
    actor text,
    action text,
    organization_uid text,
    cemetery_uid text,
    detail text
);


--
-- Name: cp_audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.cp_audit_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.cp_audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: cp_binding_issuers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_binding_issuers (
    binding_uid text NOT NULL,
    principal_uid text NOT NULL,
    organization_uid text NOT NULL
);


--
-- Name: cp_bindings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_bindings (
    binding_uid text NOT NULL,
    device_uid text NOT NULL,
    organization_uid text NOT NULL,
    cemetery_uid text NOT NULL,
    key_hash text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    sync_epoch integer DEFAULT 1 NOT NULL,
    last_change_seq bigint DEFAULT 0 NOT NULL,
    CONSTRAINT cp_bindings_key_hash_check CHECK ((key_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT cp_bindings_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'active'::text, 'revoked'::text])))
);


--
-- Name: cp_bootstrap_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_bootstrap_runs (
    bootstrap_id text NOT NULL,
    organization_uid text NOT NULL,
    cemetery_uid text NOT NULL,
    kind text NOT NULL,
    binding_uid text,
    installation_uid text,
    plan_hash text NOT NULL,
    state text NOT NULL,
    checkpoint text DEFAULT '{}'::text NOT NULL,
    created_at double precision NOT NULL,
    finished_at double precision,
    error text,
    CONSTRAINT cp_bootstrap_runs_bootstrap_id_check CHECK ((bootstrap_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'::text)),
    CONSTRAINT cp_bootstrap_runs_cemetery_uid_check CHECK ((cemetery_uid ~ '^[0-9a-f-]{36}$'::text)),
    CONSTRAINT cp_bootstrap_runs_kind_check CHECK ((kind = ANY (ARRAY['A'::text, 'B'::text]))),
    CONSTRAINT cp_bootstrap_runs_state_check CHECK ((state = ANY (ARRAY['planned'::text, 'running'::text, 'verifying'::text, 'completed'::text, 'failed'::text, 'rolled_back'::text])))
);


--
-- Name: cp_cemeteries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_cemeteries (
    uid text NOT NULL,
    organization_uid text NOT NULL,
    name text NOT NULL,
    status text DEFAULT 'DRAFT'::text NOT NULL,
    data_epoch integer DEFAULT 1 NOT NULL,
    CONSTRAINT cp_cemeteries_data_epoch_check CHECK ((data_epoch >= 1)),
    CONSTRAINT cp_cemeteries_status_check CHECK ((status = ANY (ARRAY['DRAFT'::text, 'ACTIVE'::text, 'ARCHIVED'::text]))),
    CONSTRAINT cp_cemeteries_uid_check CHECK ((uid ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'::text))
);


--
-- Name: cp_devices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_devices (
    device_uid text NOT NULL,
    organization_uid text NOT NULL,
    installation_uid text NOT NULL,
    name text
);


--
-- Name: cp_failed_redeems; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_failed_redeems (
    installation_uid text NOT NULL,
    n integer DEFAULT 0 NOT NULL
);


--
-- Name: cp_media_blobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_media_blobs (
    cemetery_uid text NOT NULL,
    sha256 text NOT NULL,
    size bigint NOT NULL,
    data bytea NOT NULL,
    organization_uid text NOT NULL,
    CONSTRAINT cp_media_blobs_sha256_check CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: cp_media_objects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_media_objects (
    cemetery_uid text NOT NULL,
    uid text NOT NULL,
    kind text DEFAULT 'document'::text NOT NULL,
    entity_type text,
    entity_id text,
    name text NOT NULL,
    size bigint NOT NULL,
    sha256 text NOT NULL,
    present integer DEFAULT 1 NOT NULL,
    organization_uid text NOT NULL,
    CONSTRAINT cp_media_objects_present_check CHECK ((present = ANY (ARRAY[0, 1]))),
    CONSTRAINT cp_media_objects_size_check CHECK ((size >= 0))
);


--
-- Name: cp_media_published; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_media_published (
    cemetery_uid text NOT NULL,
    uid text NOT NULL,
    sha256 text NOT NULL,
    size bigint NOT NULL,
    published_at double precision NOT NULL,
    organization_uid text NOT NULL,
    CONSTRAINT cp_media_published_sha256_check CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: cp_memberships; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_memberships (
    principal_uid text NOT NULL,
    organization_uid text NOT NULL,
    role text NOT NULL,
    CONSTRAINT cp_memberships_role_check CHECK ((role = ANY (ARRAY['owner'::text, 'rebuild'::text, 'admin'::text])))
);


--
-- Name: cp_open_conflicts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_open_conflicts (
    cemetery_uid text NOT NULL,
    uid text NOT NULL,
    organization_uid text NOT NULL
);


--
-- Name: cp_organizations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_organizations (
    uid text NOT NULL,
    name text NOT NULL,
    status text DEFAULT 'ACTIVE'::text NOT NULL,
    tombstone_retention_days integer DEFAULT 180 NOT NULL,
    CONSTRAINT cp_organizations_uid_check CHECK ((uid ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'::text))
);


--
-- Name: cp_pairing_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_pairing_codes (
    code_hash text NOT NULL,
    organization_uid text NOT NULL,
    cemetery_uid text,
    issued_by text NOT NULL,
    expires_at double precision NOT NULL,
    used_at double precision,
    used_bootstrap_id text,
    CONSTRAINT cp_pairing_codes_code_hash_check CHECK ((code_hash ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: cp_principals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_principals (
    uid text NOT NULL,
    name text NOT NULL,
    platform_admin integer DEFAULT 0 NOT NULL,
    CONSTRAINT cp_principals_platform_admin_check CHECK ((platform_admin = ANY (ARRAY[0, 1])))
);


--
-- Name: cp_registry_row_data; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_registry_row_data (
    cemetery_uid text NOT NULL,
    entity text NOT NULL,
    uid text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    content text DEFAULT '{}'::text NOT NULL,
    change_seq bigint DEFAULT 0 NOT NULL,
    organization_uid text NOT NULL
);


--
-- Name: cp_registry_rows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_registry_rows (
    cemetery_uid text NOT NULL,
    entity text NOT NULL,
    uid text NOT NULL,
    organization_uid text NOT NULL
);


--
-- Name: cp_staging_batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_staging_batches (
    bootstrap_id text NOT NULL,
    cemetery_uid text NOT NULL,
    entity_type text NOT NULL,
    batch_no integer NOT NULL,
    batch_count integer NOT NULL,
    batch_sha256 text NOT NULL,
    row_count integer NOT NULL,
    received_at double precision NOT NULL,
    organization_uid text NOT NULL
);


--
-- Name: cp_staging_media_blobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_staging_media_blobs (
    bootstrap_id text NOT NULL,
    cemetery_uid text NOT NULL,
    uid text NOT NULL,
    sha256 text NOT NULL,
    size integer NOT NULL,
    chunk_count integer NOT NULL,
    state text NOT NULL,
    organization_uid text NOT NULL
);


--
-- Name: cp_staging_media_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_staging_media_chunks (
    bootstrap_id text NOT NULL,
    cemetery_uid text NOT NULL,
    uid text NOT NULL,
    n integer NOT NULL,
    sha256 text NOT NULL,
    size integer NOT NULL,
    data bytea NOT NULL,
    organization_uid text NOT NULL
);


--
-- Name: cp_staging_rows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_staging_rows (
    bootstrap_id text NOT NULL,
    cemetery_uid text NOT NULL,
    entity_type text NOT NULL,
    uid text NOT NULL,
    batch_no integer NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    content text NOT NULL,
    organization_uid text NOT NULL
);


--
-- Name: cp_staging_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_staging_sessions (
    bootstrap_id text NOT NULL,
    organization_uid text NOT NULL,
    cemetery_uid text NOT NULL,
    plan_hash text NOT NULL,
    source_manifest_digest text NOT NULL,
    schema_version text NOT NULL,
    data_epoch integer NOT NULL,
    entity_plan text NOT NULL,
    state text DEFAULT 'open'::text NOT NULL,
    created_at double precision NOT NULL,
    last_activity_at double precision DEFAULT 0 NOT NULL,
    stale_after double precision DEFAULT 86400 NOT NULL,
    CONSTRAINT cp_staging_sessions_state_check CHECK ((state = ANY (ARRAY['open'::text, 'stale'::text, 'cleaned'::text, 'published'::text])))
);


--
-- Name: cp_tombstones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cp_tombstones (
    cemetery_uid text NOT NULL,
    entity text NOT NULL,
    uid text NOT NULL,
    organization_uid text NOT NULL,
    tombstone_id bigint NOT NULL
);


--
-- Name: cp_tombstones_tombstone_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.cp_tombstones ALTER COLUMN tombstone_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.cp_tombstones_tombstone_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: device_cemetery_bindings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.device_cemetery_bindings (
    binding_uid uuid DEFAULT gen_random_uuid() NOT NULL,
    device_uid uuid NOT NULL,
    organization_uid uuid NOT NULL,
    cemetery_uid uuid NOT NULL,
    sync_key_hash text NOT NULL,
    key_prefix text NOT NULL,
    sync_epoch uuid,
    last_change_seq bigint DEFAULT 0 NOT NULL,
    pending_ops integer,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone,
    last_sync_at timestamp with time zone,
    revoked_at timestamp with time zone,
    revoked_by text,
    replaced_by_binding_uid uuid
);


--
-- Name: device_pairing_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.device_pairing_codes (
    code_hash text NOT NULL,
    organization_uid uuid NOT NULL,
    cemetery_uid uuid NOT NULL,
    device_type text DEFAULT 'MAIN'::text NOT NULL,
    created_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    used_by_binding_uid uuid
);


--
-- Name: devices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.devices (
    device_uid uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_uid uuid NOT NULL,
    installation_uid uuid NOT NULL,
    device_name text NOT NULL,
    device_type text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    app_version text,
    platform text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text,
    last_seen_at timestamp with time zone,
    CONSTRAINT devices_device_type_check CHECK ((device_type = ANY (ARRAY['MAIN'::text, 'MOBILE'::text, 'WEB_KIOSK'::text, 'SERVICE'::text])))
);


--
-- Name: documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.documents (
    id bigint NOT NULL,
    uid text DEFAULT (gen_random_uuid())::text NOT NULL,
    entity_type text NOT NULL,
    entity_id text NOT NULL,
    doc_type text,
    file_path text NOT NULL,
    caption text,
    nr_evidenta text,
    date_added text,
    sha256 text,
    size bigint,
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    media_id text,
    description text,
    original_filename text,
    mime_type text,
    uploaded_by text,
    source text,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.documents ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.documents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: email_outbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.email_outbox (
    id bigint NOT NULL,
    cod_urmarire text,
    template text,
    to_email text,
    to_masked text,
    subject text,
    body text,
    status text DEFAULT 'pending'::text,
    attempts integer DEFAULT 0,
    last_error text,
    created_at text,
    sent_at text,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    organization_uid uuid DEFAULT public.app_org_uid(),
    cemetery_uid uuid
);


--
-- Name: email_outbox_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.email_outbox ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.email_outbox_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: guides; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guides (
    id bigint NOT NULL,
    uid text DEFAULT (gen_random_uuid())::text NOT NULL,
    kind text,
    points jsonb DEFAULT '[]'::jsonb NOT NULL,
    color text,
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: guides_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.guides ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.guides_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: identities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.identities (
    uid uuid DEFAULT gen_random_uuid() NOT NULL,
    display_name text NOT NULL,
    email text,
    password_hash text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_login_at timestamp with time zone,
    legacy_user_id bigint
);


--
-- Name: inhumati; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inhumati (
    id bigint NOT NULL,
    uid text DEFAULT (gen_random_uuid())::text NOT NULL,
    nume text,
    prenume text,
    cnp text,
    data_nasterii text,
    data_deces text,
    data_inhumare text,
    data_deshumare text,
    sex text,
    cauza_deces text,
    profesie text,
    religie text,
    ultima_adresa text,
    observatii text,
    nr_certificat_deces text,
    nr_adeverinta_inhumare text,
    id_loc bigint,
    contract_id bigint,
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    version integer DEFAULT 0 NOT NULL,
    change_seq bigint,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: inhumati_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.inhumati ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.inhumati_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: loc_extra; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.loc_extra (
    cod_loc text NOT NULL,
    extra_json jsonb,
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: loc_holders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.loc_holders (
    cod_loc text NOT NULL,
    holder_hash text NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: locuri_de_veci; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.locuri_de_veci (
    id bigint NOT NULL,
    uid text DEFAULT (gen_random_uuid())::text NOT NULL,
    cod_loc text NOT NULL,
    points jsonb DEFAULT '[]'::jsonb NOT NULL,
    color text,
    sector text,
    parcela text,
    rand text,
    numar text,
    capacitate text,
    disponibilitate text,
    tip text,
    stare text,
    categorie text,
    suprafata double precision,
    nr_vechi text,
    numar_cf text,
    ocupat integer DEFAULT 0,
    observatii text,
    extra_data text DEFAULT '{}'::text,
    sync_version integer DEFAULT 0,
    are_inhumari integer DEFAULT 0,
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    version integer DEFAULT 0 NOT NULL,
    change_seq bigint,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: locuri_de_veci_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.locuri_de_veci ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.locuri_de_veci_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: media_objects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media_objects (
    id text NOT NULL,
    entity_uid text NOT NULL,
    filename text NOT NULL,
    storage_path text NOT NULL,
    mime text DEFAULT 'application/octet-stream'::text NOT NULL,
    size bigint NOT NULL,
    sha256 text NOT NULL,
    uploaded_by text,
    uploaded_at timestamp with time zone DEFAULT now() NOT NULL,
    change_seq bigint,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL,
    CONSTRAINT media_objects_sha256_check CHECK ((length(sha256) = 64)),
    CONSTRAINT media_objects_size_check CHECK ((size >= 0))
);


--
-- Name: mobile_upload_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mobile_upload_tokens (
    token_hash text NOT NULL,
    tid text NOT NULL,
    entity_type text NOT NULL,
    entity_id text NOT NULL,
    entity_uid text,
    created_by text,
    created_at bigint NOT NULL,
    expires_at bigint NOT NULL,
    max_uses integer DEFAULT 1 NOT NULL,
    uses integer DEFAULT 0 NOT NULL,
    revoked integer DEFAULT 0 NOT NULL,
    last_doc_id integer,
    used_at bigint,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: organization_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.organization_members (
    member_uid uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_uid uuid NOT NULL,
    identity_uid uuid NOT NULL,
    username text NOT NULL,
    org_role text DEFAULT 'STAFF'::text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT organization_members_org_role_check CHECK ((org_role = ANY (ARRAY['ORG_ADMIN'::text, 'STAFF'::text])))
);


--
-- Name: organizations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.organizations (
    uid uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_type text NOT NULL,
    official_name text NOT NULL,
    display_name text NOT NULL,
    cui_cif text,
    county text,
    locality text,
    address text,
    phone text,
    email text,
    logo_media_uid uuid,
    terminology_profile text,
    labels jsonb DEFAULT '{}'::jsonb NOT NULL,
    locale text DEFAULT 'ro-RO'::text NOT NULL,
    timezone text DEFAULT 'Europe/Bucharest'::text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    CONSTRAINT organizations_organization_type_check CHECK ((organization_type = ANY (ARRAY['PARISH'::text, 'MUNICIPALITY'::text, 'PRIVATE_OPERATOR'::text, 'MONASTERY'::text, 'ASSOCIATION'::text, 'OTHER'::text])))
);


--
-- Name: photos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.photos (
    id bigint NOT NULL,
    uid text DEFAULT (gen_random_uuid())::text NOT NULL,
    cod_loc text NOT NULL,
    file_path text NOT NULL,
    caption text,
    uploader text,
    date_added text,
    is_cover integer DEFAULT 0,
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    media_id text,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: photos_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.photos ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.photos_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: plati; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.plati (
    id bigint NOT NULL,
    uid text DEFAULT (gen_random_uuid())::text NOT NULL,
    cod_loc text,
    contract_id bigint,
    suma double precision,
    data_plata text,
    tip text,
    chitanta_nr text,
    observatii text,
    created_at text DEFAULT to_char((now() AT TIME ZONE 'utc'::text), 'YYYY-MM-DD HH24:MI:SS'::text),
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    version integer DEFAULT 0 NOT NULL,
    change_seq bigint,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: plati_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.plati ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.plati_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qr_generations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qr_generations (
    id bigint NOT NULL,
    uid text DEFAULT (gen_random_uuid())::text NOT NULL,
    loc_id bigint,
    cod_loc text NOT NULL,
    generated_at text NOT NULL,
    generated_by text,
    format text NOT NULL,
    path text,
    printed_at text,
    printed_by text,
    notes text,
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: qr_generations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qr_generations ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qr_generations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: rows_ghid; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rows_ghid (
    id bigint NOT NULL,
    uid text DEFAULT (gen_random_uuid())::text NOT NULL,
    sector_label text,
    name text,
    order_index integer,
    baseline jsonb DEFAULT '[]'::jsonb NOT NULL,
    width double precision,
    depth double precision,
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: rows_ghid_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.rows_ghid ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.rows_ghid_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sectoare; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sectoare (
    id text NOT NULL,
    uid text DEFAULT (gen_random_uuid())::text NOT NULL,
    points jsonb DEFAULT '[]'::jsonb NOT NULL,
    color text,
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.settings (
    key text NOT NULL,
    value text,
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: sync_change_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sync_change_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sync_conflicts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sync_conflicts (
    id bigint NOT NULL,
    op_id text NOT NULL,
    entity text NOT NULL,
    uid text NOT NULL,
    action text NOT NULL,
    reason text NOT NULL,
    client_id text,
    base_version integer,
    server_version integer,
    client_data text,
    server_data text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    resolution text,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: sync_conflicts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.sync_conflicts ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.sync_conflicts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sync_ops; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sync_ops (
    op_id text NOT NULL,
    kind text,
    applied_at text,
    result text,
    created_ts timestamp with time zone DEFAULT now() NOT NULL,
    response text,
    client_id text,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: sync_tombstones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sync_tombstones (
    entity text NOT NULL,
    uid text NOT NULL,
    version integer NOT NULL,
    change_seq bigint NOT NULL,
    deleted_at timestamp with time zone DEFAULT now() NOT NULL,
    organization_uid uuid DEFAULT public.app_org_uid() NOT NULL,
    cemetery_uid uuid DEFAULT public.app_default_cemetery_uid() NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id bigint NOT NULL,
    uid text DEFAULT (gen_random_uuid())::text NOT NULL,
    username text NOT NULL,
    password_hash text NOT NULL,
    role text DEFAULT 'user'::text NOT NULL,
    created_at text NOT NULL,
    last_login text,
    rights text,
    active integer DEFAULT 1,
    cemetery_id text,
    version integer DEFAULT 1,
    updated_at text,
    origin text DEFAULT 'local'::text,
    lease_until text,
    modified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_ts timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.users ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ai_scan_history ai_scan_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_scan_history
    ADD CONSTRAINT ai_scan_history_pkey PRIMARY KEY (id);


--
-- Name: ai_scan_history ai_scan_history_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_scan_history
    ADD CONSTRAINT ai_scan_history_uid_key UNIQUE (uid);


--
-- Name: amenajari_avize amenajari_avize_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.amenajari_avize
    ADD CONSTRAINT amenajari_avize_pkey PRIMARY KEY (id);


--
-- Name: amenajari_avize amenajari_avize_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.amenajari_avize
    ADD CONSTRAINT amenajari_avize_uid_key UNIQUE (uid);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: cemeteries cemeteries_organization_uid_internal_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cemeteries
    ADD CONSTRAINT cemeteries_organization_uid_internal_code_key UNIQUE (organization_uid, internal_code);


--
-- Name: cemeteries cemeteries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cemeteries
    ADD CONSTRAINT cemeteries_pkey PRIMARY KEY (uid);


--
-- Name: cemeteries cemeteries_public_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cemeteries
    ADD CONSTRAINT cemeteries_public_slug_key UNIQUE (public_slug);


--
-- Name: cemeteries cemeteries_uid_organization_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cemeteries
    ADD CONSTRAINT cemeteries_uid_organization_uid_key UNIQUE (uid, organization_uid);


--
-- Name: cemetery_permissions cemetery_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cemetery_permissions
    ADD CONSTRAINT cemetery_permissions_pkey PRIMARY KEY (member_uid, cemetery_uid);


--
-- Name: cereri_concesionare cereri_concesionare_cod_urmarire_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cereri_concesionare
    ADD CONSTRAINT cereri_concesionare_cod_urmarire_key UNIQUE (cod_urmarire);


--
-- Name: cereri_concesionare cereri_concesionare_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cereri_concesionare
    ADD CONSTRAINT cereri_concesionare_pkey PRIMARY KEY (id);


--
-- Name: cereri_concesionare cereri_concesionare_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cereri_concesionare
    ADD CONSTRAINT cereri_concesionare_uid_key UNIQUE (uid);


--
-- Name: citizen_resets citizen_resets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.citizen_resets
    ADD CONSTRAINT citizen_resets_pkey PRIMARY KEY (id);


--
-- Name: citizen_resets citizen_resets_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.citizen_resets
    ADD CONSTRAINT citizen_resets_token_hash_key UNIQUE (token_hash);


--
-- Name: citizens citizens_organization_uid_id_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.citizens
    ADD CONSTRAINT citizens_organization_uid_id_uq UNIQUE (organization_uid, id);


--
-- Name: citizens citizens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.citizens
    ADD CONSTRAINT citizens_pkey PRIMARY KEY (id);


--
-- Name: citizens citizens_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.citizens
    ADD CONSTRAINT citizens_uid_key UNIQUE (uid);


--
-- Name: cloud_sync_docs cloud_sync_docs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cloud_sync_docs
    ADD CONSTRAINT cloud_sync_docs_pkey PRIMARY KEY (tracking_code, doc_id);


--
-- Name: cloud_sync_map cloud_sync_map_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cloud_sync_map
    ADD CONSTRAINT cloud_sync_map_pkey PRIMARY KEY (tracking_code);


--
-- Name: cloud_sync_outbox cloud_sync_outbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cloud_sync_outbox
    ADD CONSTRAINT cloud_sync_outbox_pkey PRIMARY KEY (id);


--
-- Name: concesionari concesionari_cemetery_uid_id_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.concesionari
    ADD CONSTRAINT concesionari_cemetery_uid_id_uq UNIQUE (cemetery_uid, id);


--
-- Name: concesionari concesionari_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.concesionari
    ADD CONSTRAINT concesionari_pkey PRIMARY KEY (id);


--
-- Name: concesionari concesionari_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.concesionari
    ADD CONSTRAINT concesionari_uid_key UNIQUE (uid);


--
-- Name: contracte contracte_cemetery_uid_id_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracte
    ADD CONSTRAINT contracte_cemetery_uid_id_uq UNIQUE (cemetery_uid, id);


--
-- Name: contracte contracte_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracte
    ADD CONSTRAINT contracte_pkey PRIMARY KEY (id);


--
-- Name: contracte contracte_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracte
    ADD CONSTRAINT contracte_uid_key UNIQUE (uid);


--
-- Name: cp_audit_log cp_audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_audit_log
    ADD CONSTRAINT cp_audit_log_pkey PRIMARY KEY (id);


--
-- Name: cp_binding_issuers cp_binding_issuers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_binding_issuers
    ADD CONSTRAINT cp_binding_issuers_pkey PRIMARY KEY (binding_uid);


--
-- Name: cp_bindings cp_bindings_device_uid_cemetery_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_bindings
    ADD CONSTRAINT cp_bindings_device_uid_cemetery_uid_key UNIQUE (device_uid, cemetery_uid);


--
-- Name: cp_bindings cp_bindings_key_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_bindings
    ADD CONSTRAINT cp_bindings_key_hash_key UNIQUE (key_hash);


--
-- Name: cp_bindings cp_bindings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_bindings
    ADD CONSTRAINT cp_bindings_pkey PRIMARY KEY (binding_uid);


--
-- Name: cp_bootstrap_runs cp_bootstrap_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_bootstrap_runs
    ADD CONSTRAINT cp_bootstrap_runs_pkey PRIMARY KEY (bootstrap_id);


--
-- Name: cp_cemeteries cp_cemeteries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_cemeteries
    ADD CONSTRAINT cp_cemeteries_pkey PRIMARY KEY (uid);


--
-- Name: cp_cemeteries cp_cemeteries_uid_organization_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_cemeteries
    ADD CONSTRAINT cp_cemeteries_uid_organization_uid_key UNIQUE (uid, organization_uid);


--
-- Name: cp_devices cp_devices_device_uid_organization_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_devices
    ADD CONSTRAINT cp_devices_device_uid_organization_uid_key UNIQUE (device_uid, organization_uid);


--
-- Name: cp_devices cp_devices_organization_uid_installation_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_devices
    ADD CONSTRAINT cp_devices_organization_uid_installation_uid_key UNIQUE (organization_uid, installation_uid);


--
-- Name: cp_devices cp_devices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_devices
    ADD CONSTRAINT cp_devices_pkey PRIMARY KEY (device_uid);


--
-- Name: cp_failed_redeems cp_failed_redeems_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_failed_redeems
    ADD CONSTRAINT cp_failed_redeems_pkey PRIMARY KEY (installation_uid);


--
-- Name: cp_media_blobs cp_media_blobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_media_blobs
    ADD CONSTRAINT cp_media_blobs_pkey PRIMARY KEY (cemetery_uid, sha256);


--
-- Name: cp_media_objects cp_media_objects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_media_objects
    ADD CONSTRAINT cp_media_objects_pkey PRIMARY KEY (cemetery_uid, uid);


--
-- Name: cp_media_published cp_media_published_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_media_published
    ADD CONSTRAINT cp_media_published_pkey PRIMARY KEY (cemetery_uid, uid);


--
-- Name: cp_memberships cp_memberships_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_memberships
    ADD CONSTRAINT cp_memberships_pkey PRIMARY KEY (principal_uid, organization_uid, role);


--
-- Name: cp_open_conflicts cp_open_conflicts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_open_conflicts
    ADD CONSTRAINT cp_open_conflicts_pkey PRIMARY KEY (cemetery_uid, uid);


--
-- Name: cp_organizations cp_organizations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_organizations
    ADD CONSTRAINT cp_organizations_pkey PRIMARY KEY (uid);


--
-- Name: cp_pairing_codes cp_pairing_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_pairing_codes
    ADD CONSTRAINT cp_pairing_codes_pkey PRIMARY KEY (code_hash);


--
-- Name: cp_principals cp_principals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_principals
    ADD CONSTRAINT cp_principals_pkey PRIMARY KEY (uid);


--
-- Name: cp_registry_row_data cp_registry_row_data_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_registry_row_data
    ADD CONSTRAINT cp_registry_row_data_pkey PRIMARY KEY (cemetery_uid, entity, uid);


--
-- Name: cp_registry_rows cp_registry_rows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_registry_rows
    ADD CONSTRAINT cp_registry_rows_pkey PRIMARY KEY (cemetery_uid, entity, uid);


--
-- Name: cp_staging_batches cp_staging_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_staging_batches
    ADD CONSTRAINT cp_staging_batches_pkey PRIMARY KEY (bootstrap_id, entity_type, batch_no);


--
-- Name: cp_staging_media_blobs cp_staging_media_blobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_staging_media_blobs
    ADD CONSTRAINT cp_staging_media_blobs_pkey PRIMARY KEY (bootstrap_id, uid);


--
-- Name: cp_staging_media_chunks cp_staging_media_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_staging_media_chunks
    ADD CONSTRAINT cp_staging_media_chunks_pkey PRIMARY KEY (bootstrap_id, uid, n);


--
-- Name: cp_staging_rows cp_staging_rows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_staging_rows
    ADD CONSTRAINT cp_staging_rows_pkey PRIMARY KEY (bootstrap_id, entity_type, uid);


--
-- Name: cp_staging_sessions cp_staging_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_staging_sessions
    ADD CONSTRAINT cp_staging_sessions_pkey PRIMARY KEY (bootstrap_id);


--
-- Name: cp_tombstones cp_tombstones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_tombstones
    ADD CONSTRAINT cp_tombstones_pkey PRIMARY KEY (tombstone_id);


--
-- Name: device_cemetery_bindings device_cemetery_bindings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.device_cemetery_bindings
    ADD CONSTRAINT device_cemetery_bindings_pkey PRIMARY KEY (binding_uid);


--
-- Name: device_cemetery_bindings device_cemetery_bindings_sync_key_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.device_cemetery_bindings
    ADD CONSTRAINT device_cemetery_bindings_sync_key_hash_key UNIQUE (sync_key_hash);


--
-- Name: device_pairing_codes device_pairing_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.device_pairing_codes
    ADD CONSTRAINT device_pairing_codes_pkey PRIMARY KEY (code_hash);


--
-- Name: devices devices_device_uid_organization_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devices
    ADD CONSTRAINT devices_device_uid_organization_uid_key UNIQUE (device_uid, organization_uid);


--
-- Name: devices devices_organization_uid_installation_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devices
    ADD CONSTRAINT devices_organization_uid_installation_uid_key UNIQUE (organization_uid, installation_uid);


--
-- Name: devices devices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devices
    ADD CONSTRAINT devices_pkey PRIMARY KEY (device_uid);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: documents documents_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_uid_key UNIQUE (uid);


--
-- Name: email_outbox email_outbox_cod_urmarire_template_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_outbox
    ADD CONSTRAINT email_outbox_cod_urmarire_template_key UNIQUE (cod_urmarire, template);


--
-- Name: email_outbox email_outbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_outbox
    ADD CONSTRAINT email_outbox_pkey PRIMARY KEY (id);


--
-- Name: guides guides_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guides
    ADD CONSTRAINT guides_pkey PRIMARY KEY (id);


--
-- Name: guides guides_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guides
    ADD CONSTRAINT guides_uid_key UNIQUE (uid);


--
-- Name: identities identities_legacy_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.identities
    ADD CONSTRAINT identities_legacy_user_id_key UNIQUE (legacy_user_id);


--
-- Name: identities identities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.identities
    ADD CONSTRAINT identities_pkey PRIMARY KEY (uid);


--
-- Name: inhumati inhumati_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inhumati
    ADD CONSTRAINT inhumati_pkey PRIMARY KEY (id);


--
-- Name: inhumati inhumati_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inhumati
    ADD CONSTRAINT inhumati_uid_key UNIQUE (uid);


--
-- Name: loc_extra loc_extra_cemetery_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.loc_extra
    ADD CONSTRAINT loc_extra_cemetery_pkey PRIMARY KEY (cemetery_uid, cod_loc);


--
-- Name: loc_holders loc_holders_cemetery_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.loc_holders
    ADD CONSTRAINT loc_holders_cemetery_pkey PRIMARY KEY (cemetery_uid, cod_loc, holder_hash);


--
-- Name: locuri_de_veci locuri_de_veci_cemetery_cod_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.locuri_de_veci
    ADD CONSTRAINT locuri_de_veci_cemetery_cod_uq UNIQUE (cemetery_uid, cod_loc);


--
-- Name: locuri_de_veci locuri_de_veci_cemetery_uid_id_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.locuri_de_veci
    ADD CONSTRAINT locuri_de_veci_cemetery_uid_id_uq UNIQUE (cemetery_uid, id);


--
-- Name: locuri_de_veci locuri_de_veci_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.locuri_de_veci
    ADD CONSTRAINT locuri_de_veci_pkey PRIMARY KEY (id);


--
-- Name: locuri_de_veci locuri_de_veci_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.locuri_de_veci
    ADD CONSTRAINT locuri_de_veci_uid_key UNIQUE (uid);


--
-- Name: media_objects media_objects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_objects
    ADD CONSTRAINT media_objects_pkey PRIMARY KEY (id);


--
-- Name: media_objects media_objects_storage_path_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_objects
    ADD CONSTRAINT media_objects_storage_path_key UNIQUE (storage_path);


--
-- Name: mobile_upload_tokens mobile_upload_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mobile_upload_tokens
    ADD CONSTRAINT mobile_upload_tokens_pkey PRIMARY KEY (token_hash);


--
-- Name: organization_members organization_members_member_uid_organization_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organization_members
    ADD CONSTRAINT organization_members_member_uid_organization_uid_key UNIQUE (member_uid, organization_uid);


--
-- Name: organization_members organization_members_organization_uid_identity_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organization_members
    ADD CONSTRAINT organization_members_organization_uid_identity_uid_key UNIQUE (organization_uid, identity_uid);


--
-- Name: organization_members organization_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organization_members
    ADD CONSTRAINT organization_members_pkey PRIMARY KEY (member_uid);


--
-- Name: organizations organizations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organizations
    ADD CONSTRAINT organizations_pkey PRIMARY KEY (uid);


--
-- Name: photos photos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.photos
    ADD CONSTRAINT photos_pkey PRIMARY KEY (id);


--
-- Name: photos photos_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.photos
    ADD CONSTRAINT photos_uid_key UNIQUE (uid);


--
-- Name: plati plati_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plati
    ADD CONSTRAINT plati_pkey PRIMARY KEY (id);


--
-- Name: plati plati_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plati
    ADD CONSTRAINT plati_uid_key UNIQUE (uid);


--
-- Name: qr_generations qr_generations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qr_generations
    ADD CONSTRAINT qr_generations_pkey PRIMARY KEY (id);


--
-- Name: qr_generations qr_generations_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qr_generations
    ADD CONSTRAINT qr_generations_uid_key UNIQUE (uid);


--
-- Name: rows_ghid rows_ghid_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rows_ghid
    ADD CONSTRAINT rows_ghid_pkey PRIMARY KEY (id);


--
-- Name: rows_ghid rows_ghid_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rows_ghid
    ADD CONSTRAINT rows_ghid_uid_key UNIQUE (uid);


--
-- Name: sectoare sectoare_cemetery_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sectoare
    ADD CONSTRAINT sectoare_cemetery_pkey PRIMARY KEY (cemetery_uid, id);


--
-- Name: sectoare sectoare_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sectoare
    ADD CONSTRAINT sectoare_uid_key UNIQUE (uid);


--
-- Name: settings settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settings
    ADD CONSTRAINT settings_pkey PRIMARY KEY (key);


--
-- Name: sync_conflicts sync_conflicts_op_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_conflicts
    ADD CONSTRAINT sync_conflicts_op_id_key UNIQUE (op_id);


--
-- Name: sync_conflicts sync_conflicts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_conflicts
    ADD CONSTRAINT sync_conflicts_pkey PRIMARY KEY (id);


--
-- Name: sync_ops sync_ops_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_ops
    ADD CONSTRAINT sync_ops_pkey PRIMARY KEY (op_id);


--
-- Name: sync_tombstones sync_tombstones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_tombstones
    ADD CONSTRAINT sync_tombstones_pkey PRIMARY KEY (entity, uid);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_uid_key UNIQUE (uid);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: ai_scan_history_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ai_scan_history_cemetery_idx ON public.ai_scan_history USING btree (cemetery_uid);


--
-- Name: amenajari_avize_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX amenajari_avize_cemetery_idx ON public.amenajari_avize USING btree (cemetery_uid);


--
-- Name: audit_log_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX audit_log_cemetery_idx ON public.audit_log USING btree (cemetery_uid);


--
-- Name: cereri_concesionare_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX cereri_concesionare_cemetery_idx ON public.cereri_concesionare USING btree (cemetery_uid);


--
-- Name: citizen_resets_org_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX citizen_resets_org_idx ON public.citizen_resets USING btree (organization_uid);


--
-- Name: citizens_org_email_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX citizens_org_email_uq ON public.citizens USING btree (organization_uid, lower(email));


--
-- Name: citizens_org_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX citizens_org_idx ON public.citizens USING btree (organization_uid);


--
-- Name: concesionari_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX concesionari_cemetery_idx ON public.concesionari USING btree (cemetery_uid);


--
-- Name: contracte_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX contracte_cemetery_idx ON public.contracte USING btree (cemetery_uid);


--
-- Name: cp_one_active_bootstrap; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX cp_one_active_bootstrap ON public.cp_bootstrap_runs USING btree (cemetery_uid) WHERE (state = ANY (ARRAY['planned'::text, 'running'::text, 'verifying'::text]));


--
-- Name: cp_one_open_staging_per_cemetery; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX cp_one_open_staging_per_cemetery ON public.cp_staging_sessions USING btree (cemetery_uid) WHERE (state = 'open'::text);


--
-- Name: device_binding_active_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX device_binding_active_uq ON public.device_cemetery_bindings USING btree (device_uid, cemetery_uid) WHERE (revoked_at IS NULL);


--
-- Name: documents_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX documents_cemetery_idx ON public.documents USING btree (cemetery_uid);


--
-- Name: email_outbox_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX email_outbox_cemetery_idx ON public.email_outbox USING btree (cemetery_uid);


--
-- Name: guides_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX guides_cemetery_idx ON public.guides USING btree (cemetery_uid);


--
-- Name: identities_email_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX identities_email_uq ON public.identities USING btree (lower(email)) WHERE (email IS NOT NULL);


--
-- Name: idx_amenajari_avize_change_seq; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_amenajari_avize_change_seq ON public.amenajari_avize USING btree (change_seq);


--
-- Name: idx_audit_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_action ON public.audit_log USING btree (action);


--
-- Name: idx_audit_actor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_actor ON public.audit_log USING btree (actor);


--
-- Name: idx_audit_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_ts ON public.audit_log USING btree (ts);


--
-- Name: idx_avize_codloc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_avize_codloc ON public.amenajari_avize USING btree (cod_loc);


--
-- Name: idx_cereri_citizen; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cereri_citizen ON public.cereri_concesionare USING btree (citizen_id);


--
-- Name: idx_cereri_codloc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cereri_codloc ON public.cereri_concesionare USING btree (cod_loc);


--
-- Name: idx_cereri_contract; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cereri_contract ON public.cereri_concesionare USING btree (contract_id);


--
-- Name: idx_cereri_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cereri_status ON public.cereri_concesionare USING btree (status);


--
-- Name: idx_citizen_resets_cit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_citizen_resets_cit ON public.citizen_resets USING btree (citizen_id);


--
-- Name: idx_cloud_outbox_tracking; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cloud_outbox_tracking ON public.cloud_sync_outbox USING btree (tracking_code);


--
-- Name: idx_concesionari_change_seq; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_concesionari_change_seq ON public.concesionari USING btree (change_seq);


--
-- Name: idx_concesionari_cnp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_concesionari_cnp ON public.concesionari USING btree (cnp);


--
-- Name: idx_concesionari_nume; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_concesionari_nume ON public.concesionari USING btree (nume);


--
-- Name: idx_contracte_change_seq; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contracte_change_seq ON public.contracte USING btree (change_seq);


--
-- Name: idx_contracte_cod_loc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contracte_cod_loc ON public.contracte USING btree (cod_loc);


--
-- Name: idx_contracte_concesionar; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contracte_concesionar ON public.contracte USING btree (concesionar_id);


--
-- Name: idx_contracte_loc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contracte_loc ON public.contracte USING btree (id_loc);


--
-- Name: idx_contracte_numar; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contracte_numar ON public.contracte USING btree (numar_contract);


--
-- Name: idx_contracte_sfarsit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contracte_sfarsit ON public.contracte USING btree (data_sfarsit);


--
-- Name: idx_documents_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_entity ON public.documents USING btree (entity_type, entity_id);


--
-- Name: idx_documents_media; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_media ON public.documents USING btree (media_id);


--
-- Name: idx_documents_sha256; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_sha256 ON public.documents USING btree (sha256);


--
-- Name: idx_documents_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_type ON public.documents USING btree (entity_type, doc_type);


--
-- Name: idx_email_outbox_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_email_outbox_status ON public.email_outbox USING btree (status);


--
-- Name: idx_inhumati_change_seq; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inhumati_change_seq ON public.inhumati USING btree (change_seq);


--
-- Name: idx_inhumati_cnp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inhumati_cnp ON public.inhumati USING btree (cnp);


--
-- Name: idx_inhumati_contract; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inhumati_contract ON public.inhumati USING btree (contract_id);


--
-- Name: idx_inhumati_data; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inhumati_data ON public.inhumati USING btree (data_inhumare);


--
-- Name: idx_inhumati_loc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inhumati_loc ON public.inhumati USING btree (id_loc);


--
-- Name: idx_inhumati_nume; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inhumati_nume ON public.inhumati USING btree (nume, prenume);


--
-- Name: idx_loc_holders_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_loc_holders_hash ON public.loc_holders USING btree (holder_hash);


--
-- Name: idx_locuri_de_veci_change_seq; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_locuri_de_veci_change_seq ON public.locuri_de_veci USING btree (change_seq);


--
-- Name: idx_locuri_modified; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_locuri_modified ON public.locuri_de_veci USING btree (modified_at);


--
-- Name: idx_locuri_nr_vechi; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_locuri_nr_vechi ON public.locuri_de_veci USING btree (nr_vechi);


--
-- Name: idx_locuri_numar_cf; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_locuri_numar_cf ON public.locuri_de_veci USING btree (numar_cf);


--
-- Name: idx_locuri_sector; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_locuri_sector ON public.locuri_de_veci USING btree (sector);


--
-- Name: idx_locuri_stare; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_locuri_stare ON public.locuri_de_veci USING btree (stare);


--
-- Name: idx_media_objects_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_media_objects_entity ON public.media_objects USING btree (entity_uid);


--
-- Name: idx_media_objects_seq; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_media_objects_seq ON public.media_objects USING btree (change_seq);


--
-- Name: idx_mobile_tokens_exp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mobile_tokens_exp ON public.mobile_upload_tokens USING btree (expires_at);


--
-- Name: idx_mobile_tokens_tid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mobile_tokens_tid ON public.mobile_upload_tokens USING btree (tid);


--
-- Name: idx_photos_codloc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_photos_codloc ON public.photos USING btree (cod_loc);


--
-- Name: idx_photos_cover; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_photos_cover ON public.photos USING btree (cod_loc, is_cover);


--
-- Name: idx_photos_media; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_photos_media ON public.photos USING btree (media_id);


--
-- Name: idx_plati_change_seq; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_plati_change_seq ON public.plati USING btree (change_seq);


--
-- Name: idx_plati_codloc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_plati_codloc ON public.plati USING btree (cod_loc);


--
-- Name: idx_plati_contract; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_plati_contract ON public.plati USING btree (contract_id);


--
-- Name: idx_plati_data; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_plati_data ON public.plati USING btree (data_plata);


--
-- Name: idx_qr_cod_loc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_qr_cod_loc ON public.qr_generations USING btree (cod_loc);


--
-- Name: idx_qr_generated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_qr_generated_at ON public.qr_generations USING btree (generated_at);


--
-- Name: idx_rows_sector; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rows_sector ON public.rows_ghid USING btree (sector_label, order_index);


--
-- Name: idx_scan_history_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_scan_history_created ON public.ai_scan_history USING btree (created_at DESC);


--
-- Name: idx_sync_conflicts_open; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sync_conflicts_open ON public.sync_conflicts USING btree (resolved_at) WHERE (resolved_at IS NULL);


--
-- Name: idx_tombstones_seq; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tombstones_seq ON public.sync_tombstones USING btree (entity, change_seq);


--
-- Name: inhumati_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX inhumati_cemetery_idx ON public.inhumati USING btree (cemetery_uid);


--
-- Name: loc_extra_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX loc_extra_cemetery_idx ON public.loc_extra USING btree (cemetery_uid);


--
-- Name: loc_holders_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX loc_holders_cemetery_idx ON public.loc_holders USING btree (cemetery_uid);


--
-- Name: locuri_de_veci_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX locuri_de_veci_cemetery_idx ON public.locuri_de_veci USING btree (cemetery_uid);


--
-- Name: media_objects_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX media_objects_cemetery_idx ON public.media_objects USING btree (cemetery_uid);


--
-- Name: mobile_upload_tokens_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX mobile_upload_tokens_cemetery_idx ON public.mobile_upload_tokens USING btree (cemetery_uid);


--
-- Name: organization_members_username_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX organization_members_username_uq ON public.organization_members USING btree (organization_uid, lower(username));


--
-- Name: photos_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX photos_cemetery_idx ON public.photos USING btree (cemetery_uid);


--
-- Name: plati_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX plati_cemetery_idx ON public.plati USING btree (cemetery_uid);


--
-- Name: qr_generations_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX qr_generations_cemetery_idx ON public.qr_generations USING btree (cemetery_uid);


--
-- Name: rows_ghid_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX rows_ghid_cemetery_idx ON public.rows_ghid USING btree (cemetery_uid);


--
-- Name: sectoare_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX sectoare_cemetery_idx ON public.sectoare USING btree (cemetery_uid);


--
-- Name: sync_conflicts_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX sync_conflicts_cemetery_idx ON public.sync_conflicts USING btree (cemetery_uid);


--
-- Name: sync_ops_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX sync_ops_cemetery_idx ON public.sync_ops USING btree (cemetery_uid);


--
-- Name: sync_tombstones_cemetery_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX sync_tombstones_cemetery_idx ON public.sync_tombstones USING btree (cemetery_uid);


--
-- Name: cp_binding_issuers cp_fill_org; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER cp_fill_org BEFORE INSERT ON public.cp_binding_issuers FOR EACH ROW EXECUTE FUNCTION public.cp_fill_org();


--
-- Name: cp_media_blobs cp_fill_org; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER cp_fill_org BEFORE INSERT ON public.cp_media_blobs FOR EACH ROW EXECUTE FUNCTION public.cp_fill_org();


--
-- Name: cp_media_objects cp_fill_org; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER cp_fill_org BEFORE INSERT ON public.cp_media_objects FOR EACH ROW EXECUTE FUNCTION public.cp_fill_org();


--
-- Name: cp_media_published cp_fill_org; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER cp_fill_org BEFORE INSERT ON public.cp_media_published FOR EACH ROW EXECUTE FUNCTION public.cp_fill_org();


--
-- Name: cp_open_conflicts cp_fill_org; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER cp_fill_org BEFORE INSERT ON public.cp_open_conflicts FOR EACH ROW EXECUTE FUNCTION public.cp_fill_org();


--
-- Name: cp_registry_row_data cp_fill_org; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER cp_fill_org BEFORE INSERT ON public.cp_registry_row_data FOR EACH ROW EXECUTE FUNCTION public.cp_fill_org();


--
-- Name: cp_registry_rows cp_fill_org; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER cp_fill_org BEFORE INSERT ON public.cp_registry_rows FOR EACH ROW EXECUTE FUNCTION public.cp_fill_org();


--
-- Name: cp_staging_batches cp_fill_org; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER cp_fill_org BEFORE INSERT ON public.cp_staging_batches FOR EACH ROW EXECUTE FUNCTION public.cp_fill_org();


--
-- Name: cp_staging_media_blobs cp_fill_org; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER cp_fill_org BEFORE INSERT ON public.cp_staging_media_blobs FOR EACH ROW EXECUTE FUNCTION public.cp_fill_org();


--
-- Name: cp_staging_media_chunks cp_fill_org; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER cp_fill_org BEFORE INSERT ON public.cp_staging_media_chunks FOR EACH ROW EXECUTE FUNCTION public.cp_fill_org();


--
-- Name: cp_staging_rows cp_fill_org; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER cp_fill_org BEFORE INSERT ON public.cp_staging_rows FOR EACH ROW EXECUTE FUNCTION public.cp_fill_org();


--
-- Name: cp_tombstones cp_fill_org; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER cp_fill_org BEFORE INSERT ON public.cp_tombstones FOR EACH ROW EXECUTE FUNCTION public.cp_fill_org();


--
-- Name: ai_scan_history trg_ai_scan_history_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ai_scan_history_modified BEFORE UPDATE ON public.ai_scan_history FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: amenajari_avize trg_amenajari_avize_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_amenajari_avize_modified BEFORE UPDATE ON public.amenajari_avize FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: amenajari_avize trg_amenajari_avize_sync_tomb; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_amenajari_avize_sync_tomb AFTER DELETE ON public.amenajari_avize FOR EACH ROW EXECUTE FUNCTION public.cimitir_sync_tombstone();


--
-- Name: amenajari_avize trg_amenajari_avize_sync_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_amenajari_avize_sync_touch BEFORE INSERT OR UPDATE ON public.amenajari_avize FOR EACH ROW EXECUTE FUNCTION public.cimitir_sync_touch();


--
-- Name: cereri_concesionare trg_cereri_concesionare_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_cereri_concesionare_modified BEFORE UPDATE ON public.cereri_concesionare FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: citizens trg_citizens_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_citizens_modified BEFORE UPDATE ON public.citizens FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: concesionari trg_concesionari_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_concesionari_modified BEFORE UPDATE ON public.concesionari FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: concesionari trg_concesionari_sync_tomb; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_concesionari_sync_tomb AFTER DELETE ON public.concesionari FOR EACH ROW EXECUTE FUNCTION public.cimitir_sync_tombstone();


--
-- Name: concesionari trg_concesionari_sync_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_concesionari_sync_touch BEFORE INSERT OR UPDATE ON public.concesionari FOR EACH ROW EXECUTE FUNCTION public.cimitir_sync_touch();


--
-- Name: contracte trg_contracte_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_contracte_modified BEFORE UPDATE ON public.contracte FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: contracte trg_contracte_sync_tomb; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_contracte_sync_tomb AFTER DELETE ON public.contracte FOR EACH ROW EXECUTE FUNCTION public.cimitir_sync_tombstone();


--
-- Name: contracte trg_contracte_sync_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_contracte_sync_touch BEFORE INSERT OR UPDATE ON public.contracte FOR EACH ROW EXECUTE FUNCTION public.cimitir_sync_touch();


--
-- Name: documents trg_documents_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_documents_modified BEFORE UPDATE ON public.documents FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: guides trg_guides_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_guides_modified BEFORE UPDATE ON public.guides FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: inhumati trg_inhumati_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_inhumati_modified BEFORE UPDATE ON public.inhumati FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: inhumati trg_inhumati_sync_tomb; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_inhumati_sync_tomb AFTER DELETE ON public.inhumati FOR EACH ROW EXECUTE FUNCTION public.cimitir_sync_tombstone();


--
-- Name: inhumati trg_inhumati_sync_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_inhumati_sync_touch BEFORE INSERT OR UPDATE ON public.inhumati FOR EACH ROW EXECUTE FUNCTION public.cimitir_sync_touch();


--
-- Name: loc_extra trg_loc_extra_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_loc_extra_modified BEFORE UPDATE ON public.loc_extra FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: locuri_de_veci trg_locuri_de_veci_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_locuri_de_veci_modified BEFORE UPDATE ON public.locuri_de_veci FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: locuri_de_veci trg_locuri_de_veci_sync_tomb; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_locuri_de_veci_sync_tomb AFTER DELETE ON public.locuri_de_veci FOR EACH ROW EXECUTE FUNCTION public.cimitir_sync_tombstone();


--
-- Name: locuri_de_veci trg_locuri_de_veci_sync_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_locuri_de_veci_sync_touch BEFORE INSERT OR UPDATE ON public.locuri_de_veci FOR EACH ROW EXECUTE FUNCTION public.cimitir_sync_touch();


--
-- Name: media_objects trg_media_objects_tomb; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_media_objects_tomb AFTER DELETE ON public.media_objects FOR EACH ROW EXECUTE FUNCTION public.cimitir_media_tombstone();


--
-- Name: media_objects trg_media_objects_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_media_objects_touch BEFORE INSERT ON public.media_objects FOR EACH ROW EXECUTE FUNCTION public.cimitir_media_touch();


--
-- Name: photos trg_photos_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_photos_modified BEFORE UPDATE ON public.photos FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: plati trg_plati_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_plati_modified BEFORE UPDATE ON public.plati FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: plati trg_plati_sync_tomb; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_plati_sync_tomb AFTER DELETE ON public.plati FOR EACH ROW EXECUTE FUNCTION public.cimitir_sync_tombstone();


--
-- Name: plati trg_plati_sync_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_plati_sync_touch BEFORE INSERT OR UPDATE ON public.plati FOR EACH ROW EXECUTE FUNCTION public.cimitir_sync_touch();


--
-- Name: qr_generations trg_qr_generations_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qr_generations_modified BEFORE UPDATE ON public.qr_generations FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: rows_ghid trg_rows_ghid_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rows_ghid_modified BEFORE UPDATE ON public.rows_ghid FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: sectoare trg_sectoare_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_sectoare_modified BEFORE UPDATE ON public.sectoare FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: settings trg_settings_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_settings_modified BEFORE UPDATE ON public.settings FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: users trg_users_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_users_modified BEFORE UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION public.cimitir_set_modified_at();


--
-- Name: amenajari_avize amenajari_avize_cod_loc_fkey_tenant; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.amenajari_avize
    ADD CONSTRAINT amenajari_avize_cod_loc_fkey_tenant FOREIGN KEY (cemetery_uid, cod_loc) REFERENCES public.locuri_de_veci(cemetery_uid, cod_loc) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: cemeteries cemeteries_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cemeteries
    ADD CONSTRAINT cemeteries_organization_uid_fkey FOREIGN KEY (organization_uid) REFERENCES public.organizations(uid);


--
-- Name: cemetery_permissions cemetery_permissions_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cemetery_permissions
    ADD CONSTRAINT cemetery_permissions_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cemeteries(uid, organization_uid);


--
-- Name: cemetery_permissions cemetery_permissions_member_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cemetery_permissions
    ADD CONSTRAINT cemetery_permissions_member_uid_organization_uid_fkey FOREIGN KEY (member_uid, organization_uid) REFERENCES public.organization_members(member_uid, organization_uid);


--
-- Name: cereri_concesionare cereri_concesionare_citizen_id_fkey_tenant; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cereri_concesionare
    ADD CONSTRAINT cereri_concesionare_citizen_id_fkey_tenant FOREIGN KEY (organization_uid, citizen_id) REFERENCES public.citizens(organization_uid, id) ON DELETE SET NULL (citizen_id);


--
-- Name: cereri_concesionare cereri_concesionare_concesionar_id_fkey_tenant; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cereri_concesionare
    ADD CONSTRAINT cereri_concesionare_concesionar_id_fkey_tenant FOREIGN KEY (cemetery_uid, concesionar_id) REFERENCES public.concesionari(cemetery_uid, id) ON DELETE SET NULL (concesionar_id);


--
-- Name: citizen_resets citizen_resets_citizen_id_fkey_tenant; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.citizen_resets
    ADD CONSTRAINT citizen_resets_citizen_id_fkey_tenant FOREIGN KEY (organization_uid, citizen_id) REFERENCES public.citizens(organization_uid, id) ON DELETE CASCADE;


--
-- Name: contracte contracte_concesionar_id_fkey_tenant; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracte
    ADD CONSTRAINT contracte_concesionar_id_fkey_tenant FOREIGN KEY (cemetery_uid, concesionar_id) REFERENCES public.concesionari(cemetery_uid, id) ON UPDATE CASCADE ON DELETE SET NULL (concesionar_id);


--
-- Name: contracte contracte_id_loc_fkey_tenant; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracte
    ADD CONSTRAINT contracte_id_loc_fkey_tenant FOREIGN KEY (cemetery_uid, id_loc) REFERENCES public.locuri_de_veci(cemetery_uid, id) ON UPDATE CASCADE ON DELETE SET NULL (id_loc);


--
-- Name: cp_binding_issuers cp_binding_issuers_binding_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_binding_issuers
    ADD CONSTRAINT cp_binding_issuers_binding_uid_fkey FOREIGN KEY (binding_uid) REFERENCES public.cp_bindings(binding_uid);


--
-- Name: cp_bindings cp_bindings_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_bindings
    ADD CONSTRAINT cp_bindings_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cp_cemeteries(uid, organization_uid);


--
-- Name: cp_bindings cp_bindings_device_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_bindings
    ADD CONSTRAINT cp_bindings_device_uid_organization_uid_fkey FOREIGN KEY (device_uid, organization_uid) REFERENCES public.cp_devices(device_uid, organization_uid);


--
-- Name: cp_bootstrap_runs cp_bootstrap_runs_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_bootstrap_runs
    ADD CONSTRAINT cp_bootstrap_runs_organization_uid_fkey FOREIGN KEY (organization_uid) REFERENCES public.cp_organizations(uid);


--
-- Name: cp_cemeteries cp_cemeteries_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_cemeteries
    ADD CONSTRAINT cp_cemeteries_organization_uid_fkey FOREIGN KEY (organization_uid) REFERENCES public.cp_organizations(uid);


--
-- Name: cp_devices cp_devices_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_devices
    ADD CONSTRAINT cp_devices_organization_uid_fkey FOREIGN KEY (organization_uid) REFERENCES public.cp_organizations(uid);


--
-- Name: cp_media_blobs cp_media_blobs_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_media_blobs
    ADD CONSTRAINT cp_media_blobs_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cp_cemeteries(uid, organization_uid);


--
-- Name: cp_media_objects cp_media_objects_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_media_objects
    ADD CONSTRAINT cp_media_objects_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cp_cemeteries(uid, organization_uid);


--
-- Name: cp_media_published cp_media_published_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_media_published
    ADD CONSTRAINT cp_media_published_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cp_cemeteries(uid, organization_uid);


--
-- Name: cp_memberships cp_memberships_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_memberships
    ADD CONSTRAINT cp_memberships_organization_uid_fkey FOREIGN KEY (organization_uid) REFERENCES public.cp_organizations(uid);


--
-- Name: cp_memberships cp_memberships_principal_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_memberships
    ADD CONSTRAINT cp_memberships_principal_uid_fkey FOREIGN KEY (principal_uid) REFERENCES public.cp_principals(uid);


--
-- Name: cp_open_conflicts cp_open_conflicts_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_open_conflicts
    ADD CONSTRAINT cp_open_conflicts_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cp_cemeteries(uid, organization_uid);


--
-- Name: cp_pairing_codes cp_pairing_codes_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_pairing_codes
    ADD CONSTRAINT cp_pairing_codes_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cp_cemeteries(uid, organization_uid);


--
-- Name: cp_pairing_codes cp_pairing_codes_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_pairing_codes
    ADD CONSTRAINT cp_pairing_codes_organization_uid_fkey FOREIGN KEY (organization_uid) REFERENCES public.cp_organizations(uid);


--
-- Name: cp_registry_row_data cp_registry_row_data_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_registry_row_data
    ADD CONSTRAINT cp_registry_row_data_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cp_cemeteries(uid, organization_uid);


--
-- Name: cp_registry_rows cp_registry_rows_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_registry_rows
    ADD CONSTRAINT cp_registry_rows_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cp_cemeteries(uid, organization_uid);


--
-- Name: cp_staging_batches cp_staging_batches_bootstrap_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_staging_batches
    ADD CONSTRAINT cp_staging_batches_bootstrap_id_fkey FOREIGN KEY (bootstrap_id) REFERENCES public.cp_staging_sessions(bootstrap_id);


--
-- Name: cp_staging_batches cp_staging_batches_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_staging_batches
    ADD CONSTRAINT cp_staging_batches_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cp_cemeteries(uid, organization_uid);


--
-- Name: cp_staging_media_blobs cp_staging_media_blobs_bootstrap_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_staging_media_blobs
    ADD CONSTRAINT cp_staging_media_blobs_bootstrap_id_fkey FOREIGN KEY (bootstrap_id) REFERENCES public.cp_staging_sessions(bootstrap_id);


--
-- Name: cp_staging_media_blobs cp_staging_media_blobs_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_staging_media_blobs
    ADD CONSTRAINT cp_staging_media_blobs_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cp_cemeteries(uid, organization_uid);


--
-- Name: cp_staging_media_chunks cp_staging_media_chunks_bootstrap_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_staging_media_chunks
    ADD CONSTRAINT cp_staging_media_chunks_bootstrap_id_fkey FOREIGN KEY (bootstrap_id) REFERENCES public.cp_staging_sessions(bootstrap_id);


--
-- Name: cp_staging_media_chunks cp_staging_media_chunks_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_staging_media_chunks
    ADD CONSTRAINT cp_staging_media_chunks_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cp_cemeteries(uid, organization_uid);


--
-- Name: cp_staging_rows cp_staging_rows_bootstrap_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_staging_rows
    ADD CONSTRAINT cp_staging_rows_bootstrap_id_fkey FOREIGN KEY (bootstrap_id) REFERENCES public.cp_staging_sessions(bootstrap_id);


--
-- Name: cp_staging_rows cp_staging_rows_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_staging_rows
    ADD CONSTRAINT cp_staging_rows_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cp_cemeteries(uid, organization_uid);


--
-- Name: cp_staging_sessions cp_staging_sessions_bootstrap_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_staging_sessions
    ADD CONSTRAINT cp_staging_sessions_bootstrap_id_fkey FOREIGN KEY (bootstrap_id) REFERENCES public.cp_bootstrap_runs(bootstrap_id);


--
-- Name: cp_staging_sessions cp_staging_sessions_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_staging_sessions
    ADD CONSTRAINT cp_staging_sessions_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cp_cemeteries(uid, organization_uid);


--
-- Name: cp_tombstones cp_tombstones_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cp_tombstones
    ADD CONSTRAINT cp_tombstones_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cp_cemeteries(uid, organization_uid);


--
-- Name: device_cemetery_bindings device_cemetery_bindings_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.device_cemetery_bindings
    ADD CONSTRAINT device_cemetery_bindings_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cemeteries(uid, organization_uid);


--
-- Name: device_cemetery_bindings device_cemetery_bindings_device_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.device_cemetery_bindings
    ADD CONSTRAINT device_cemetery_bindings_device_uid_organization_uid_fkey FOREIGN KEY (device_uid, organization_uid) REFERENCES public.devices(device_uid, organization_uid);


--
-- Name: device_pairing_codes device_pairing_codes_cemetery_uid_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.device_pairing_codes
    ADD CONSTRAINT device_pairing_codes_cemetery_uid_organization_uid_fkey FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES public.cemeteries(uid, organization_uid);


--
-- Name: devices devices_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devices
    ADD CONSTRAINT devices_organization_uid_fkey FOREIGN KEY (organization_uid) REFERENCES public.organizations(uid);


--
-- Name: cereri_concesionare fk_cereri_contract_tenant; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cereri_concesionare
    ADD CONSTRAINT fk_cereri_contract_tenant FOREIGN KEY (cemetery_uid, contract_id) REFERENCES public.contracte(cemetery_uid, id) ON UPDATE CASCADE ON DELETE SET NULL (contract_id);


--
-- Name: plati fk_plati_contract_tenant; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plati
    ADD CONSTRAINT fk_plati_contract_tenant FOREIGN KEY (cemetery_uid, contract_id) REFERENCES public.contracte(cemetery_uid, id) ON UPDATE CASCADE ON DELETE SET NULL (contract_id);


--
-- Name: qr_generations fk_qr_loc_tenant; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qr_generations
    ADD CONSTRAINT fk_qr_loc_tenant FOREIGN KEY (cemetery_uid, loc_id) REFERENCES public.locuri_de_veci(cemetery_uid, id) ON UPDATE CASCADE ON DELETE SET NULL (loc_id);


--
-- Name: inhumati inhumati_contract_id_fkey_tenant; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inhumati
    ADD CONSTRAINT inhumati_contract_id_fkey_tenant FOREIGN KEY (cemetery_uid, contract_id) REFERENCES public.contracte(cemetery_uid, id) ON UPDATE CASCADE ON DELETE SET NULL (contract_id);


--
-- Name: inhumati inhumati_id_loc_fkey_tenant; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inhumati
    ADD CONSTRAINT inhumati_id_loc_fkey_tenant FOREIGN KEY (cemetery_uid, id_loc) REFERENCES public.locuri_de_veci(cemetery_uid, id) ON UPDATE CASCADE ON DELETE SET NULL (id_loc);


--
-- Name: organization_members organization_members_identity_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organization_members
    ADD CONSTRAINT organization_members_identity_uid_fkey FOREIGN KEY (identity_uid) REFERENCES public.identities(uid);


--
-- Name: organization_members organization_members_organization_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organization_members
    ADD CONSTRAINT organization_members_organization_uid_fkey FOREIGN KEY (organization_uid) REFERENCES public.organizations(uid);


--
-- Name: photos photos_cod_loc_fkey_tenant; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.photos
    ADD CONSTRAINT photos_cod_loc_fkey_tenant FOREIGN KEY (cemetery_uid, cod_loc) REFERENCES public.locuri_de_veci(cemetery_uid, cod_loc) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: ai_scan_history; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.ai_scan_history ENABLE ROW LEVEL SECURITY;

--
-- Name: amenajari_avize; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.amenajari_avize ENABLE ROW LEVEL SECURITY;

--
-- Name: audit_log; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;

--
-- Name: cemeteries; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cemeteries ENABLE ROW LEVEL SECURITY;

--
-- Name: cemetery_permissions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cemetery_permissions ENABLE ROW LEVEL SECURITY;

--
-- Name: cereri_concesionare; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cereri_concesionare ENABLE ROW LEVEL SECURITY;

--
-- Name: citizen_resets; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.citizen_resets ENABLE ROW LEVEL SECURITY;

--
-- Name: citizens; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.citizens ENABLE ROW LEVEL SECURITY;

--
-- Name: concesionari; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.concesionari ENABLE ROW LEVEL SECURITY;

--
-- Name: contracte; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.contracte ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_audit_log; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_audit_log ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_binding_issuers; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_binding_issuers ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_bindings; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_bindings ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_bootstrap_runs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_bootstrap_runs ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_cemeteries; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_cemeteries ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_devices; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_devices ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_failed_redeems; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_failed_redeems ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_media_blobs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_media_blobs ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_media_objects; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_media_objects ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_media_published; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_media_published ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_memberships; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_memberships ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_open_conflicts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_open_conflicts ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_organizations; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_organizations ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_pairing_codes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_pairing_codes ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_principals; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_principals ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_registry_row_data; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_registry_row_data ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_registry_rows; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_registry_rows ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_staging_batches; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_staging_batches ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_staging_media_blobs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_staging_media_blobs ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_staging_media_chunks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_staging_media_chunks ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_staging_rows; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_staging_rows ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_staging_sessions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_staging_sessions ENABLE ROW LEVEL SECURITY;

--
-- Name: cp_audit_log cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_audit_log TO cimitir_cp_app USING ((public.cp_org_ok(organization_uid) AND ((cemetery_uid IS NULL) OR public.cp_cem_ok(cemetery_uid)))) WITH CHECK ((public.cp_platform() OR ((organization_uid = public.cp_org()) AND ((cemetery_uid IS NULL) OR public.cp_cem_ok(cemetery_uid)))));


--
-- Name: cp_binding_issuers cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_binding_issuers TO cimitir_cp_app USING ((public.cp_org_ok(organization_uid) OR (EXISTS ( SELECT 1
   FROM public.cp_bindings b
  WHERE (b.binding_uid = cp_binding_issuers.binding_uid))))) WITH CHECK (public.cp_org_ok(organization_uid));


--
-- Name: cp_bindings cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_bindings TO cimitir_cp_app USING (((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid)) OR ((binding_uid = public.cp_binding_uid()) AND (key_hash = public.cp_key_hash())))) WITH CHECK ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid)));


--
-- Name: cp_bootstrap_runs cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_bootstrap_runs TO cimitir_cp_app USING ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid))) WITH CHECK ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid)));


--
-- Name: cp_cemeteries cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_cemeteries TO cimitir_cp_app USING ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(uid))) WITH CHECK ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(uid)));


--
-- Name: cp_devices cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_devices TO cimitir_cp_app USING (public.cp_org_ok(organization_uid)) WITH CHECK (public.cp_org_ok(organization_uid));


--
-- Name: cp_failed_redeems cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_failed_redeems TO cimitir_cp_app USING (true) WITH CHECK (true);


--
-- Name: cp_media_blobs cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_media_blobs TO cimitir_cp_app USING ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid))) WITH CHECK ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid)));


--
-- Name: cp_media_objects cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_media_objects TO cimitir_cp_app USING ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid))) WITH CHECK ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid)));


--
-- Name: cp_media_published cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_media_published TO cimitir_cp_app USING ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid))) WITH CHECK ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid)));


--
-- Name: cp_memberships cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_memberships TO cimitir_cp_app USING (public.cp_org_ok(organization_uid)) WITH CHECK (public.cp_org_ok(organization_uid));


--
-- Name: cp_open_conflicts cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_open_conflicts TO cimitir_cp_app USING ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid))) WITH CHECK ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid)));


--
-- Name: cp_organizations cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_organizations TO cimitir_cp_app USING (public.cp_org_ok(uid)) WITH CHECK (public.cp_platform());


--
-- Name: cp_pairing_codes cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_pairing_codes TO cimitir_cp_app USING (((public.cp_org_ok(organization_uid) AND ((cemetery_uid IS NULL) OR public.cp_cem_ok(cemetery_uid))) OR (code_hash = public.cp_code_hash()))) WITH CHECK ((public.cp_org_ok(organization_uid) AND ((cemetery_uid IS NULL) OR public.cp_cem_ok(cemetery_uid))));


--
-- Name: cp_principals cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_principals TO cimitir_cp_app USING ((public.cp_platform() OR (EXISTS ( SELECT 1
   FROM public.cp_memberships m
  WHERE (m.principal_uid = cp_principals.uid))))) WITH CHECK (public.cp_platform());


--
-- Name: cp_registry_row_data cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_registry_row_data TO cimitir_cp_app USING ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid))) WITH CHECK ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid)));


--
-- Name: cp_registry_rows cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_registry_rows TO cimitir_cp_app USING ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid))) WITH CHECK ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid)));


--
-- Name: cp_staging_batches cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_staging_batches TO cimitir_cp_app USING ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid))) WITH CHECK ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid)));


--
-- Name: cp_staging_media_blobs cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_staging_media_blobs TO cimitir_cp_app USING ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid))) WITH CHECK ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid)));


--
-- Name: cp_staging_media_chunks cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_staging_media_chunks TO cimitir_cp_app USING ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid))) WITH CHECK ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid)));


--
-- Name: cp_staging_rows cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_staging_rows TO cimitir_cp_app USING ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid))) WITH CHECK ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid)));


--
-- Name: cp_staging_sessions cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_staging_sessions TO cimitir_cp_app USING ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid))) WITH CHECK ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid)));


--
-- Name: cp_tombstones cp_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cp_tenant ON public.cp_tombstones TO cimitir_cp_app USING ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid))) WITH CHECK ((public.cp_org_ok(organization_uid) AND public.cp_cem_ok(cemetery_uid)));


--
-- Name: cp_tombstones; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cp_tombstones ENABLE ROW LEVEL SECURITY;

--
-- Name: device_cemetery_bindings; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.device_cemetery_bindings ENABLE ROW LEVEL SECURITY;

--
-- Name: device_pairing_codes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.device_pairing_codes ENABLE ROW LEVEL SECURITY;

--
-- Name: devices; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.devices ENABLE ROW LEVEL SECURITY;

--
-- Name: documents; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;

--
-- Name: email_outbox; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.email_outbox ENABLE ROW LEVEL SECURITY;

--
-- Name: guides; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.guides ENABLE ROW LEVEL SECURITY;

--
-- Name: inhumati; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.inhumati ENABLE ROW LEVEL SECURITY;

--
-- Name: loc_extra; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.loc_extra ENABLE ROW LEVEL SECURITY;

--
-- Name: loc_holders; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.loc_holders ENABLE ROW LEVEL SECURITY;

--
-- Name: locuri_de_veci; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.locuri_de_veci ENABLE ROW LEVEL SECURITY;

--
-- Name: media_objects; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.media_objects ENABLE ROW LEVEL SECURITY;

--
-- Name: mobile_upload_tokens; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.mobile_upload_tokens ENABLE ROW LEVEL SECURITY;

--
-- Name: organization_members; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.organization_members ENABLE ROW LEVEL SECURITY;

--
-- Name: organizations; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;

--
-- Name: photos; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.photos ENABLE ROW LEVEL SECURITY;

--
-- Name: plati; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.plati ENABLE ROW LEVEL SECURITY;

--
-- Name: qr_generations; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qr_generations ENABLE ROW LEVEL SECURITY;

--
-- Name: rows_ghid; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.rows_ghid ENABLE ROW LEVEL SECURITY;

--
-- Name: sectoare; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.sectoare ENABLE ROW LEVEL SECURITY;

--
-- Name: sync_conflicts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.sync_conflicts ENABLE ROW LEVEL SECURITY;

--
-- Name: sync_ops; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.sync_ops ENABLE ROW LEVEL SECURITY;

--
-- Name: sync_tombstones; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.sync_tombstones ENABLE ROW LEVEL SECURITY;

--
-- Name: ai_scan_history tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.ai_scan_history USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: amenajari_avize tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.amenajari_avize USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: audit_log tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.audit_log USING (((organization_uid = public.app_org_uid()) AND ((cemetery_uid IS NULL) OR (cemetery_uid = ANY (public.app_cemetery_uids()))))) WITH CHECK (((organization_uid = public.app_org_uid()) AND ((cemetery_uid IS NULL) OR (cemetery_uid = ANY (public.app_cemetery_uids())))));


--
-- Name: cemeteries tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.cemeteries USING (((organization_uid = public.app_org_uid()) AND (uid = ANY (public.app_cemetery_uids())))) WITH CHECK ((organization_uid = public.app_org_uid()));


--
-- Name: cemetery_permissions tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.cemetery_permissions USING ((organization_uid = public.app_org_uid())) WITH CHECK ((organization_uid = public.app_org_uid()));


--
-- Name: cereri_concesionare tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.cereri_concesionare USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: citizen_resets tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.citizen_resets USING ((organization_uid = public.app_org_uid())) WITH CHECK ((organization_uid = public.app_org_uid()));


--
-- Name: citizens tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.citizens USING ((organization_uid = public.app_org_uid())) WITH CHECK ((organization_uid = public.app_org_uid()));


--
-- Name: concesionari tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.concesionari USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: contracte tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.contracte USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: device_cemetery_bindings tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.device_cemetery_bindings USING ((organization_uid = public.app_org_uid())) WITH CHECK ((organization_uid = public.app_org_uid()));


--
-- Name: device_pairing_codes tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.device_pairing_codes USING ((organization_uid = public.app_org_uid())) WITH CHECK ((organization_uid = public.app_org_uid()));


--
-- Name: devices tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.devices USING ((organization_uid = public.app_org_uid())) WITH CHECK ((organization_uid = public.app_org_uid()));


--
-- Name: documents tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.documents USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: email_outbox tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.email_outbox USING (((organization_uid = public.app_org_uid()) AND ((cemetery_uid IS NULL) OR (cemetery_uid = ANY (public.app_cemetery_uids()))))) WITH CHECK (((organization_uid = public.app_org_uid()) AND ((cemetery_uid IS NULL) OR (cemetery_uid = ANY (public.app_cemetery_uids())))));


--
-- Name: guides tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.guides USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: inhumati tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.inhumati USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: loc_extra tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.loc_extra USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: loc_holders tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.loc_holders USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: locuri_de_veci tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.locuri_de_veci USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: media_objects tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.media_objects USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: mobile_upload_tokens tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.mobile_upload_tokens USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: organization_members tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.organization_members USING ((organization_uid = public.app_org_uid())) WITH CHECK ((organization_uid = public.app_org_uid()));


--
-- Name: organizations tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.organizations USING ((uid = public.app_org_uid())) WITH CHECK ((uid = public.app_org_uid()));


--
-- Name: photos tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.photos USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: plati tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.plati USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: qr_generations tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.qr_generations USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: rows_ghid tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.rows_ghid USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: sectoare tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.sectoare USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: sync_conflicts tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.sync_conflicts USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: sync_ops tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.sync_ops USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: sync_tombstones tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.sync_tombstones USING (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids())))) WITH CHECK (((organization_uid = public.app_org_uid()) AND (cemetery_uid = ANY (public.app_cemetery_uids()))));


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: -
--

GRANT USAGE ON SCHEMA public TO cimitir_app;
GRANT USAGE ON SCHEMA public TO cimitir_cp_app;


--
-- Name: FUNCTION app_cemetery_uids(); Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON FUNCTION public.app_cemetery_uids() TO cimitir_app;


--
-- Name: FUNCTION app_default_cemetery_uid(); Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON FUNCTION public.app_default_cemetery_uid() TO cimitir_app;


--
-- Name: FUNCTION app_org_uid(); Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON FUNCTION public.app_org_uid() TO cimitir_app;


--
-- Name: FUNCTION cimitir_concat_step(state text, v text); Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON FUNCTION public.cimitir_concat_step(state text, v text) TO cimitir_app;


--
-- Name: FUNCTION cimitir_media_tombstone(); Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON FUNCTION public.cimitir_media_tombstone() TO cimitir_app;


--
-- Name: FUNCTION cimitir_media_touch(); Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON FUNCTION public.cimitir_media_touch() TO cimitir_app;


--
-- Name: FUNCTION cimitir_reset_sequences(); Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON FUNCTION public.cimitir_reset_sequences() TO cimitir_app;


--
-- Name: FUNCTION cimitir_set_modified_at(); Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON FUNCTION public.cimitir_set_modified_at() TO cimitir_app;


--
-- Name: FUNCTION cimitir_sync_tombstone(); Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON FUNCTION public.cimitir_sync_tombstone() TO cimitir_app;


--
-- Name: FUNCTION cimitir_sync_touch(); Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON FUNCTION public.cimitir_sync_touch() TO cimitir_app;


--
-- Name: FUNCTION cp_cemetery_org(cem text); Type: ACL; Schema: public; Owner: -
--

REVOKE ALL ON FUNCTION public.cp_cemetery_org(cem text) FROM PUBLIC;
GRANT ALL ON FUNCTION public.cp_cemetery_org(cem text) TO cimitir_cp_app;


--
-- Name: FUNCTION group_concat(text); Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON FUNCTION public.group_concat(text) TO cimitir_app;


--
-- Name: TABLE ai_scan_history; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.ai_scan_history TO cimitir_app;


--
-- Name: SEQUENCE ai_scan_history_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.ai_scan_history_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.ai_scan_history_id_seq TO cimitir_cp_app;


--
-- Name: TABLE amenajari_avize; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.amenajari_avize TO cimitir_app;


--
-- Name: SEQUENCE amenajari_avize_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.amenajari_avize_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.amenajari_avize_id_seq TO cimitir_cp_app;


--
-- Name: TABLE audit_log; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.audit_log TO cimitir_app;


--
-- Name: SEQUENCE audit_log_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.audit_log_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.audit_log_id_seq TO cimitir_cp_app;


--
-- Name: TABLE cemeteries; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cemeteries TO cimitir_app;


--
-- Name: TABLE cemetery_permissions; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cemetery_permissions TO cimitir_app;


--
-- Name: TABLE cereri_concesionare; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cereri_concesionare TO cimitir_app;


--
-- Name: SEQUENCE cereri_concesionare_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.cereri_concesionare_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.cereri_concesionare_id_seq TO cimitir_cp_app;


--
-- Name: TABLE citizen_resets; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.citizen_resets TO cimitir_app;


--
-- Name: SEQUENCE citizen_resets_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.citizen_resets_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.citizen_resets_id_seq TO cimitir_cp_app;


--
-- Name: TABLE citizens; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.citizens TO cimitir_app;


--
-- Name: SEQUENCE citizens_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.citizens_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.citizens_id_seq TO cimitir_cp_app;


--
-- Name: TABLE cloud_sync_docs; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cloud_sync_docs TO cimitir_app;


--
-- Name: TABLE cloud_sync_map; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cloud_sync_map TO cimitir_app;


--
-- Name: TABLE cloud_sync_outbox; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cloud_sync_outbox TO cimitir_app;


--
-- Name: SEQUENCE cloud_sync_outbox_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.cloud_sync_outbox_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.cloud_sync_outbox_id_seq TO cimitir_cp_app;


--
-- Name: TABLE concesionari; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.concesionari TO cimitir_app;


--
-- Name: SEQUENCE concesionari_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.concesionari_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.concesionari_id_seq TO cimitir_cp_app;


--
-- Name: TABLE contracte; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.contracte TO cimitir_app;


--
-- Name: SEQUENCE contracte_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.contracte_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.contracte_id_seq TO cimitir_cp_app;


--
-- Name: TABLE cp_audit_log; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT ON TABLE public.cp_audit_log TO cimitir_cp_app;


--
-- Name: SEQUENCE cp_audit_log_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.cp_audit_log_id_seq TO cimitir_cp_app;


--
-- Name: TABLE cp_binding_issuers; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_binding_issuers TO cimitir_cp_app;


--
-- Name: TABLE cp_bindings; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_bindings TO cimitir_cp_app;


--
-- Name: TABLE cp_bootstrap_runs; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_bootstrap_runs TO cimitir_cp_app;


--
-- Name: TABLE cp_cemeteries; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_cemeteries TO cimitir_cp_app;


--
-- Name: TABLE cp_devices; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_devices TO cimitir_cp_app;


--
-- Name: TABLE cp_failed_redeems; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_failed_redeems TO cimitir_cp_app;


--
-- Name: TABLE cp_media_blobs; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_media_blobs TO cimitir_cp_app;


--
-- Name: TABLE cp_media_objects; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_media_objects TO cimitir_cp_app;


--
-- Name: TABLE cp_media_published; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_media_published TO cimitir_cp_app;


--
-- Name: TABLE cp_memberships; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_memberships TO cimitir_cp_app;


--
-- Name: TABLE cp_open_conflicts; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_open_conflicts TO cimitir_cp_app;


--
-- Name: TABLE cp_organizations; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_organizations TO cimitir_cp_app;


--
-- Name: TABLE cp_pairing_codes; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_pairing_codes TO cimitir_cp_app;


--
-- Name: TABLE cp_principals; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_principals TO cimitir_cp_app;


--
-- Name: TABLE cp_registry_row_data; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_registry_row_data TO cimitir_cp_app;


--
-- Name: TABLE cp_registry_rows; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_registry_rows TO cimitir_cp_app;


--
-- Name: TABLE cp_staging_batches; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_staging_batches TO cimitir_cp_app;


--
-- Name: TABLE cp_staging_media_blobs; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_staging_media_blobs TO cimitir_cp_app;


--
-- Name: TABLE cp_staging_media_chunks; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_staging_media_chunks TO cimitir_cp_app;


--
-- Name: TABLE cp_staging_rows; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_staging_rows TO cimitir_cp_app;


--
-- Name: TABLE cp_staging_sessions; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_staging_sessions TO cimitir_cp_app;


--
-- Name: TABLE cp_tombstones; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.cp_tombstones TO cimitir_cp_app;


--
-- Name: SEQUENCE cp_tombstones_tombstone_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.cp_tombstones_tombstone_id_seq TO cimitir_cp_app;


--
-- Name: TABLE device_cemetery_bindings; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.device_cemetery_bindings TO cimitir_app;


--
-- Name: TABLE device_pairing_codes; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.device_pairing_codes TO cimitir_app;


--
-- Name: TABLE devices; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.devices TO cimitir_app;


--
-- Name: TABLE documents; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.documents TO cimitir_app;


--
-- Name: SEQUENCE documents_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.documents_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.documents_id_seq TO cimitir_cp_app;


--
-- Name: TABLE email_outbox; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.email_outbox TO cimitir_app;


--
-- Name: SEQUENCE email_outbox_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.email_outbox_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.email_outbox_id_seq TO cimitir_cp_app;


--
-- Name: TABLE guides; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.guides TO cimitir_app;


--
-- Name: SEQUENCE guides_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.guides_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.guides_id_seq TO cimitir_cp_app;


--
-- Name: TABLE identities; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.identities TO cimitir_app;


--
-- Name: TABLE inhumati; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.inhumati TO cimitir_app;


--
-- Name: SEQUENCE inhumati_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.inhumati_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.inhumati_id_seq TO cimitir_cp_app;


--
-- Name: TABLE loc_extra; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.loc_extra TO cimitir_app;


--
-- Name: TABLE loc_holders; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.loc_holders TO cimitir_app;


--
-- Name: TABLE locuri_de_veci; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.locuri_de_veci TO cimitir_app;


--
-- Name: SEQUENCE locuri_de_veci_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.locuri_de_veci_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.locuri_de_veci_id_seq TO cimitir_cp_app;


--
-- Name: TABLE media_objects; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.media_objects TO cimitir_app;


--
-- Name: TABLE mobile_upload_tokens; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.mobile_upload_tokens TO cimitir_app;


--
-- Name: TABLE organization_members; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.organization_members TO cimitir_app;


--
-- Name: TABLE organizations; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.organizations TO cimitir_app;


--
-- Name: TABLE photos; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.photos TO cimitir_app;


--
-- Name: SEQUENCE photos_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.photos_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.photos_id_seq TO cimitir_cp_app;


--
-- Name: TABLE plati; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.plati TO cimitir_app;


--
-- Name: SEQUENCE plati_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.plati_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.plati_id_seq TO cimitir_cp_app;


--
-- Name: TABLE qr_generations; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.qr_generations TO cimitir_app;


--
-- Name: SEQUENCE qr_generations_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.qr_generations_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.qr_generations_id_seq TO cimitir_cp_app;


--
-- Name: TABLE rows_ghid; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.rows_ghid TO cimitir_app;


--
-- Name: SEQUENCE rows_ghid_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.rows_ghid_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.rows_ghid_id_seq TO cimitir_cp_app;


--
-- Name: TABLE sectoare; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.sectoare TO cimitir_app;


--
-- Name: TABLE settings; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.settings TO cimitir_app;


--
-- Name: SEQUENCE sync_change_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.sync_change_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.sync_change_seq TO cimitir_cp_app;


--
-- Name: TABLE sync_conflicts; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.sync_conflicts TO cimitir_app;


--
-- Name: SEQUENCE sync_conflicts_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.sync_conflicts_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.sync_conflicts_id_seq TO cimitir_cp_app;


--
-- Name: TABLE sync_ops; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.sync_ops TO cimitir_app;


--
-- Name: TABLE sync_tombstones; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.sync_tombstones TO cimitir_app;


--
-- Name: TABLE users; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.users TO cimitir_app;


--
-- Name: SEQUENCE users_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE public.users_id_seq TO cimitir_app;
GRANT SELECT,USAGE ON SEQUENCE public.users_id_seq TO cimitir_cp_app;


--
-- PostgreSQL database dump complete
--

\unrestrict beaYEmnHs9LiiHo5w1RwR0mkkh8ScBT4MefzJEpWRenhUKaSVZbffLp2Pvtms4P

