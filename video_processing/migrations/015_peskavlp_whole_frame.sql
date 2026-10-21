-- PeskaVLP now sees the whole keyframe, resized to 224x224, instead of the centre 224 of the frame
-- at 640x360 that it was trained on: the centre is a third of the width, and on GynSurg's labelled
-- frames the whole frame found more (README 1.7). Its vectors from the centre crop go, so `embed`
-- makes them again from the whole frame.

DELETE FROM keyframe_embedding
WHERE model_id = (SELECT model_id FROM embedding_model WHERE name = 'camma/peskavlp');
