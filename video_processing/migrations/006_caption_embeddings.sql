-- Semantic embeddings for the VLM captions.

CREATE TABLE caption_embedding
(
    keyframe_id BIGINT   NOT NULL REFERENCES keyframe_caption (keyframe_id) ON DELETE CASCADE,
    model_id    SMALLINT NOT NULL REFERENCES embedding_model (model_id),
    embedding   halfvec  NOT NULL,

    PRIMARY KEY (keyframe_id, model_id)
);

ALTER TABLE caption_embedding
    ALTER COLUMN embedding SET STORAGE EXTERNAL;
