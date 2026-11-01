-- 028_avize_complete_ROLLBACK.sql — NEDISTRUCTIV: revocă doar drepturile de scriere pe cele 5 coloane pentru rolul runtime.
-- Coloanele și valorile NU se șterg (nicio pierdere de date). După rollback, serverul detectează lipsa dreptului și lucrează în mod compatibil:
-- citește/scrie avizele fără cele 5 câmpuri și REFUZĂ explicit (schema_incompatible) valorile nevide trimise pentru ele, fără a le pierde în tăcere.
-- Reaplicarea 028 restabilește drepturile (idempotent).
DO $$
BEGIN
    IF to_regclass('amenajari_avize') IS NOT NULL AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cimitir_cp_app') THEN
        REVOKE INSERT (stare, executant, data_inceput, data_finalizare, observatii) ON amenajari_avize FROM cimitir_cp_app;
        REVOKE UPDATE (stare, executant, data_inceput, data_finalizare, observatii) ON amenajari_avize FROM cimitir_cp_app;
    END IF;
END $$;
