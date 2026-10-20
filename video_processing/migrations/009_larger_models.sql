-- The models for the full embed on the server. The previous ones stay registered: a database
-- embedded with them keeps working when CLIMB_EMBED_MODEL / CLIMB_TEXT_MODEL name them.

INSERT INTO embedding_model (model_id, name, dims, notes)
VALUES (2, 'google/siglip2-giant-opt-patch16-384', 1536, 'primary visual model, all collections'),
       (11, 'intfloat/multilingual-e5-large', 1024,
        'text encoder for transcript segments and captions; cross-lingual');

UPDATE embedding_model
SET notes = 'primary visual model before 009'
WHERE model_id = 1;
