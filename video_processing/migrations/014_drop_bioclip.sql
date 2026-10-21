-- BioCLIP 2.5, MVK's model next to SigLIP2 since migration 013, is taken out again. On MSC's
-- described MVK clips it found less than SigLIP2 alone, by descriptions and by species names, and
-- fused next to it no more (README 1.7). Its vectors, their index and its row go with it. 013 gave
-- it model_id 4, which names the index.

DROP INDEX IF EXISTS keyframe_embedding_m4_bq_hnsw;

DELETE FROM keyframe_embedding
WHERE model_id = (SELECT model_id FROM embedding_model WHERE name = 'imageomics/bioclip-2.5-vith14');

DELETE FROM embedding_model WHERE name = 'imageomics/bioclip-2.5-vith14';
