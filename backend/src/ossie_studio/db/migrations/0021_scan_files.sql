-- Table de travail du socle de plugins : l'inventaire des fichiers vus à la
-- dernière collecte d'une source, avec leur empreinte, leur cible de traitement
-- et l'état de traitement de chaque ligne. C'est un **index de travail**
-- (observabilité, orchestration), reconstructible depuis Git — pas la vérité :
-- la vérité reste le raw/ commité. Remis à plat à chaque collecte de la source.
CREATE TABLE scan_files (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id     uuid NOT NULL,
    branch_slug  text NOT NULL,
    source_slug  text NOT NULL,
    path         text NOT NULL,
    crc          text NOT NULL,
    cible        text NOT NULL DEFAULT 'raw',
    statut       text NOT NULL,
    message      text NOT NULL DEFAULT '',
    scanned_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_scan_files_lookup
    ON scan_files (space_id, branch_slug, source_slug, path);
