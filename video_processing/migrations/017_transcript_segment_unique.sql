-- A time range of a video holds one transcript segment. Transcript hits are mapped onto scenes by
-- time overlap, so the unique constraint's index is also the join's driving index and replaces the
-- plain one over the same columns.

-- Keeps the first of a range stored twice. transcript_embedding follows through ON DELETE CASCADE.
DELETE FROM transcript_segment a
    USING transcript_segment b
WHERE a.video_id = b.video_id
  AND a.start_ms = b.start_ms
  AND a.end_ms = b.end_ms
  AND a.segment_id > b.segment_id;

DROP INDEX transcript_segment_video_time_idx;

ALTER TABLE transcript_segment
    ADD CONSTRAINT transcript_segment_video_time_unique UNIQUE (video_id, start_ms, end_ms);
