-- SigLIP2 so400m, the optional second visual model for machines with a GPU (CLIMB_EMBED_MODEL).
-- Large is the default on every machine again; see the README on embeddings.

INSERT INTO embedding_model (model_id, name, dims, notes)
VALUES (3, 'google/siglip2-so400m-patch16-384', 1152, 'optional visual model for GPU machines');

UPDATE embedding_model
SET notes = CASE model_id
                WHEN 1 THEN 'primary visual model, all collections'
                WHEN 2 THEN 'registered, not a default'
                END
WHERE model_id IN (1, 2);
