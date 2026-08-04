-- Core schema for the V3C-scale pipeline.
--
-- Scope note: migrations own *structure* (tables, constraints, cheap btree indexes).
-- The  indexes are  NOT created here. Building them incrementally is several slower
--  than building them once afterwards, so they live in db/index_ops.py and are created
--  by `climb-pipe index build` after the load completes.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;


CREATE OR REPLACE FUNCTION immutable_unaccent(text)
    RETURNS text
    LANGUAGE sql
    IMMUTABLE STRICT PARALLEL SAFE
AS
$$
SELECT public.unaccent('public.unaccent'::regdictionary, $1)
$$;


CREATE OR REPLACE FUNCTION touch_updated_at()
    RETURNS trigger
    LANGUAGE plpgsql
AS
$$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;


-- ---------------------------------------------------------------------------
-- videos
-- ---------------------------------------------------------------------------

CREATE TABLE videos
(
    video_id    TEXT PRIMARY KEY,                       -- '00001'; identical to the DRES item name
    collection  TEXT    NOT NULL,                       -- V3C1 | V3C2 | V3C3 | MVK | GYNSURG
    fps         REAL    NOT NULL CHECK (fps > 0),
    duration_ms INTEGER NOT NULL CHECK (duration_ms >= 0),
    width       INTEGER,
    height      INTEGER,
    has_audio   BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX videos_collection_idx ON videos (collection);


-- ---------------------------------------------------------------------------
-- scenes -- ONE ROW PER MASTER SHOT


CREATE TABLE scenes
(
    scene_id    BIGSERIAL PRIMARY KEY,
    video_id    TEXT    NOT NULL REFERENCES videos (video_id) ON DELETE CASCADE,
    shot_index  INTEGER NOT NULL CHECK (shot_index >= 0),  -- ordinal within the video
    start_frame INTEGER NOT NULL CHECK (start_frame >= 0),
    end_frame   INTEGER NOT NULL,
    start_ms    INTEGER NOT NULL CHECK (start_ms >= 0),    -- stored, never recomputed from fps
    end_ms      INTEGER NOT NULL,

    CONSTRAINT scenes_frame_range_valid CHECK (end_frame >= start_frame),
    CONSTRAINT scenes_ms_range_valid CHECK (end_ms >= start_ms),
    CONSTRAINT scenes_video_shot_unique UNIQUE (video_id, shot_index),

    -- Promotes the AVS sceneKey() convention (`${videoId}_${startFrame}_${endFrame}`) from a
    -- string built in two codebases into an actual key that can be joined and referenced.
    CONSTRAINT scenes_video_range_unique UNIQUE (video_id, start_frame, end_frame)
);

CREATE INDEX scenes_video_time_idx ON scenes (video_id, start_ms);


-- ---------------------------------------------------------------------------
-- keyframes
-- (Important fix ;) )No image_path column: on-disk paths are derived from (video_id, shot_index, kf_index),
-- which removes ~12.4M redundant absolute paths and the string surgery
-- (`image_path.split('/').pop()`, `LIKE '%_kf_00010.jpg'`) that read them back out.


CREATE TABLE keyframes
(
    keyframe_id  BIGSERIAL PRIMARY KEY,
    scene_id     BIGINT   NOT NULL REFERENCES scenes (scene_id) ON DELETE CASCADE,
    video_id     TEXT     NOT NULL REFERENCES videos (video_id) ON DELETE CASCADE,
    kf_index     SMALLINT NOT NULL CHECK (kf_index >= 0),
    frame_number INTEGER  NOT NULL CHECK (frame_number >= 0),
    ts_ms        INTEGER  NOT NULL CHECK (ts_ms >= 0),
    embedding    halfvec(1024),

    CONSTRAINT keyframes_scene_kf_unique UNIQUE (scene_id, kf_index)
);


-- EXTENDED: it skips a futile compression attempt per row and keeps the heap at ~60 bytes per row so the metadata stays fully cached.
ALTER TABLE keyframes
    ALTER COLUMN embedding SET STORAGE EXTERNAL;

-- video_id is denormalized off scenes so the exclude-filter and per-video grouping in search do not need a join.
CREATE INDEX keyframes_video_id_idx ON keyframes (video_id);


-- ---------------------------------------------------------------------------
-- Text signals: OCR, VLM captions, ASR transcripts



CREATE TABLE keyframe_text
(
    keyframe_id BIGINT PRIMARY KEY REFERENCES keyframes (keyframe_id) ON DELETE CASCADE,
    ocr_text    TEXT     NOT NULL,
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('simple', immutable_unaccent(ocr_text))) STORED
);

CREATE TABLE keyframe_caption
(
    keyframe_id BIGINT PRIMARY KEY REFERENCES keyframes (keyframe_id) ON DELETE CASCADE,
    caption     TEXT     NOT NULL,
    model       TEXT     NOT NULL,
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('simple', immutable_unaccent(caption))) STORED
);

CREATE TABLE transcript_segment
(
    segment_id BIGSERIAL PRIMARY KEY,
    video_id   TEXT     NOT NULL REFERENCES videos (video_id) ON DELETE CASCADE,
    start_ms   INTEGER  NOT NULL CHECK (start_ms >= 0),
    end_ms     INTEGER  NOT NULL,
    text       TEXT     NOT NULL,
    lang       TEXT,
    tsv        tsvector GENERATED ALWAYS AS (to_tsvector('simple', immutable_unaccent(text))) STORED,

    CONSTRAINT transcript_ms_range_valid CHECK (end_ms >= start_ms)
);

-- Transcript hits are mapped onto scenes by time overlap, so this is the join's driving index.
CREATE INDEX transcript_segment_video_time_idx ON transcript_segment (video_id, start_ms, end_ms);


-- ---------------------------------------------------------------------------
-- ingest_jobs
--
-- Replaces dataset/compression.checkpoint (not crash-safe and not multithreading safe)

CREATE TABLE ingest_jobs
(
    video_id   TEXT PRIMARY KEY,
    collection TEXT        NOT NULL,
    source_uri TEXT        NOT NULL,
    stage      TEXT        NOT NULL DEFAULT 'PENDING',
    attempts   INTEGER     NOT NULL DEFAULT 0,
    last_error TEXT,
    host       TEXT,
    claimed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ingest_jobs_stage_valid CHECK (stage IN (
        'PENDING', 'FETCHED', 'DECODED', 'SELECTED', 'EMBEDDED',
        'OCR_DONE', 'CAPTIONED', 'ASR_DONE', 'LOADED', 'PURGED', 'FAILED'
        ))
);

-- Partial: the claim query never looks at finished work, and excluding it keeps this index small even once all 28,450 videos are through.
CREATE INDEX ingest_jobs_claimable_idx ON ingest_jobs (stage, collection)
    WHERE stage NOT IN ('PURGED', 'FAILED');

CREATE TRIGGER ingest_jobs_touch_updated_at
    BEFORE UPDATE
    ON ingest_jobs
    FOR EACH ROW
EXECUTE FUNCTION touch_updated_at();
