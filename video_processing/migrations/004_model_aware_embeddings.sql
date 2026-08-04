-- Move embeddings out of keyframes and key them by model.
--
-- SigLIP2 is trained on web image-text pairs and is a poor fit for two of the VBS collections:
-- laparoscopic surgery (GynSurg) is severely out of distribution, and underwater footage (MVK)
-- suffers from colour cast and needs fine-grained species discrimination that CLIP-family models
-- do not have. Those collections are small next to V3C's 3,800 hours, so running a second,
-- domain-appropriate model over just them costs very little -- but it cannot share this column,
-- because such models have different dimensions (BioCLIP and BiomedCLIP are 512, not 1024) and
-- live in a different vector space.
--
-- `embedding halfvec` deliberately carries no dimension, which pgvector allows; each model's
-- rows keep their own width and each model gets its own partial expression index, verified to
-- be chosen by the planner for both 1024- and 512-dim queries.

CREATE TABLE embedding_model
(
    model_id SMALLINT PRIMARY KEY,
    name     TEXT     NOT NULL UNIQUE,
    dims     INTEGER  NOT NULL CHECK (dims > 0),
    notes    TEXT
);

INSERT INTO embedding_model (model_id, name, dims, notes)
VALUES (1, 'google/siglip2-large-patch16-384', 1024, 'primary visual model, all collections');

CREATE TABLE keyframe_embedding
(
    keyframe_id BIGINT   NOT NULL REFERENCES keyframes (keyframe_id) ON DELETE CASCADE,
    model_id    SMALLINT NOT NULL REFERENCES embedding_model (model_id),
    embedding   halfvec  NOT NULL,

    PRIMARY KEY (keyframe_id, model_id)
);

ALTER TABLE keyframe_embedding
    ALTER COLUMN embedding SET STORAGE EXTERNAL;

ALTER TABLE keyframes
    DROP COLUMN embedding;
