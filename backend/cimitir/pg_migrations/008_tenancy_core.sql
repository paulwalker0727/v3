-- 008: nucleul multi-tenant (organizații, cimitire, identități, membri, permisiuni, dispozitive și legături dispozitiv × cimitir).
-- Aditivă, idempotentă.

CREATE TABLE IF NOT EXISTS organizations (
    uid                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_type   TEXT NOT NULL CHECK (organization_type IN
                        ('PARISH','MUNICIPALITY','PRIVATE_OPERATOR','MONASTERY','ASSOCIATION','OTHER')),
    official_name       TEXT NOT NULL,
    display_name        TEXT NOT NULL,
    cui_cif             TEXT,
    county              TEXT, locality TEXT, address TEXT, phone TEXT, email TEXT,
    logo_media_uid      UUID,
    terminology_profile TEXT,
    labels              JSONB NOT NULL DEFAULT '{}'::jsonb,
    locale              TEXT NOT NULL DEFAULT 'ro-RO',
    timezone            TEXT NOT NULL DEFAULT 'Europe/Bucharest',
    active              BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    version             INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS cemeteries (
    uid               UUID PRIMARY KEY,
    organization_uid  UUID NOT NULL REFERENCES organizations(uid),
    name              TEXT NOT NULL,
    internal_code     TEXT NOT NULL,
    locality TEXT, county TEXT, address TEXT,
    status            TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('DRAFT','ACTIVE','ARCHIVED')),
    map_config        JSONB NOT NULL DEFAULT '{}'::jsonb,
    public_slug       TEXT UNIQUE,
    public_enabled    BOOLEAN NOT NULL DEFAULT false,
    branding          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    version           INTEGER NOT NULL DEFAULT 1,
    UNIQUE (organization_uid, internal_code),
    UNIQUE (uid, organization_uid)                 -- ținta cheilor compuse: un copil nu poate contrazice organizația
);

-- Identitatea persoanei (credențiale) separată de apartenență și de drepturi.
CREATE TABLE IF NOT EXISTS identities (
    uid                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name        TEXT NOT NULL,
    email               TEXT,
    password_hash       TEXT NOT NULL,
    active              BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at       TIMESTAMPTZ,
    legacy_user_id      BIGINT UNIQUE                -- legătura cu users.id existent (backfill)
);
CREATE UNIQUE INDEX IF NOT EXISTS identities_email_uq ON identities (lower(email)) WHERE email IS NOT NULL;

CREATE TABLE IF NOT EXISTS organization_members (
    member_uid        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_uid  UUID NOT NULL REFERENCES organizations(uid),
    identity_uid      UUID NOT NULL REFERENCES identities(uid),
    username          TEXT NOT NULL,
    org_role          TEXT NOT NULL DEFAULT 'STAFF' CHECK (org_role IN ('ORG_ADMIN','STAFF')),
    active            BOOLEAN NOT NULL DEFAULT true,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (member_uid, organization_uid),
    UNIQUE (organization_uid, identity_uid)
);
CREATE UNIQUE INDEX IF NOT EXISTS organization_members_username_uq ON organization_members (organization_uid, lower(username));

CREATE TABLE IF NOT EXISTS cemetery_permissions (
    member_uid        UUID NOT NULL,
    organization_uid  UUID NOT NULL,
    cemetery_uid      UUID NOT NULL,
    role              TEXT NOT NULL DEFAULT 'user',        -- rolul legacy (admin/user) până la rolurile noi
    rights            JSONB NOT NULL DEFAULT '{}'::jsonb,
    active            BOOLEAN NOT NULL DEFAULT true,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (member_uid, cemetery_uid),
    FOREIGN KEY (member_uid, organization_uid)   REFERENCES organization_members (member_uid, organization_uid),
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);

-- Dispozitivul fizic (un PC MAIN) NU este legat de un cimitir; legăturile sunt în device_cemetery_bindings.
CREATE TABLE IF NOT EXISTS devices (
    device_uid        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_uid  UUID NOT NULL REFERENCES organizations(uid),
    installation_uid  UUID NOT NULL,
    device_name       TEXT NOT NULL,
    device_type       TEXT NOT NULL CHECK (device_type IN ('MAIN','MOBILE','WEB_KIOSK','SERVICE')),
    active            BOOLEAN NOT NULL DEFAULT true,
    app_version TEXT, platform TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        TEXT,
    last_seen_at      TIMESTAMPTZ,
    UNIQUE (device_uid, organization_uid),
    UNIQUE (organization_uid, installation_uid)
);

-- Cheia de sincronizare, cursorul și revocarea sunt PER legătură (dispozitiv × cimitir).
CREATE TABLE IF NOT EXISTS device_cemetery_bindings (
    binding_uid       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_uid        UUID NOT NULL,
    organization_uid  UUID NOT NULL,
    cemetery_uid      UUID NOT NULL,
    sync_key_hash     TEXT NOT NULL UNIQUE,
    key_prefix        TEXT NOT NULL,
    sync_epoch        UUID,
    last_change_seq   BIGINT NOT NULL DEFAULT 0,           -- cursorul confirmat de server pentru această legătură
    pending_ops       INTEGER,
    active            BOOLEAN NOT NULL DEFAULT true,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at      TIMESTAMPTZ,
    last_sync_at      TIMESTAMPTZ,
    revoked_at        TIMESTAMPTZ,
    revoked_by        TEXT,
    replaced_by_binding_uid UUID,
    FOREIGN KEY (device_uid, organization_uid)   REFERENCES devices (device_uid, organization_uid),
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);
-- cel mult o legătură activă (nerevocată) pe pereche dispozitiv × cimitir
CREATE UNIQUE INDEX IF NOT EXISTS device_binding_active_uq ON device_cemetery_bindings (device_uid, cemetery_uid) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS device_pairing_codes (
    code_hash         TEXT PRIMARY KEY,
    organization_uid  UUID NOT NULL,
    cemetery_uid      UUID NOT NULL,
    device_type       TEXT NOT NULL DEFAULT 'MAIN',
    created_by        TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ NOT NULL,
    used_at           TIMESTAMPTZ,
    used_by_binding_uid UUID,
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);
