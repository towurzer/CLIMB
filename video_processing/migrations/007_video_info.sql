-- Per-video metadata from the V3C info JSONs (see pipeline/video_info.py). Only V3C has them.

CREATE TABLE video_info
(
    video_id    TEXT PRIMARY KEY REFERENCES videos (video_id) ON DELETE CASCADE,
    title       TEXT   NOT NULL DEFAULT '',
    description TEXT   NOT NULL DEFAULT '',   -- plain text, HTML and Vimeo's filler removed
    channel     TEXT   NOT NULL DEFAULT '',
    tags        TEXT[] NOT NULL DEFAULT '{}', -- lowercased; some arrive with '?' for a lost character
    categories  TEXT[] NOT NULL DEFAULT '{}', -- 'sports', 'sports/outdoorsports': every ancestor
    upload_date DATE
);

CREATE INDEX video_info_tags_idx ON video_info USING gin (tags);
CREATE INDEX video_info_categories_idx ON video_info USING gin (categories);


-- One vector per video, from title + tags + description. At one row per video this stays in the
-- tens of thousands, so it is scanned exactly and gets no ANN index.
CREATE TABLE video_info_embedding
(
    video_id  TEXT     NOT NULL REFERENCES video_info (video_id) ON DELETE CASCADE,
    model_id  SMALLINT NOT NULL REFERENCES embedding_model (model_id),
    embedding halfvec  NOT NULL,

    PRIMARY KEY (video_id, model_id)
);

ALTER TABLE video_info_embedding
    ALTER COLUMN embedding SET STORAGE EXTERNAL;
