-- The visual models that search one collection next to SigLIP2 (CLIMB_COLLECTION_MODELS). Their
-- vectors exist only for that collection's keyframes.

INSERT INTO embedding_model (model_id, name, dims, notes)
VALUES (4, 'imageomics/bioclip-2.5-vith14', 1024, 'MVK, next to SigLIP2 (open_clip, MIT)'),
       (5, 'camma/peskavlp', 768, 'LHE, next to SigLIP2 (CC BY-NC-SA 4.0, research only)');
