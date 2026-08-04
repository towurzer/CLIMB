-- Semantic search over the transcripts.

INSERT INTO embedding_model (model_id, name, dims, notes)
VALUES (10, 'intfloat/multilingual-e5-base', 768,
        'text encoder for transcript segments and captions; cross-lingual');

CREATE TABLE transcript_embedding
(
    segment_id BIGINT   NOT NULL REFERENCES transcript_segment (segment_id) ON DELETE CASCADE,
    model_id   SMALLINT NOT NULL REFERENCES embedding_model (model_id),
    embedding  halfvec  NOT NULL,

    PRIMARY KEY (segment_id, model_id)
);

ALTER TABLE transcript_embedding
    ALTER COLUMN embedding SET STORAGE EXTERNAL;
