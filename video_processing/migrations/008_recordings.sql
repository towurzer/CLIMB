-- MVK clips are cut from longer recordings: Ambon_Apr2012_0001 .. _0008 are one dive. Searching,
-- excluding and browsing treat a recording as one video; DRES still gets the clip.
--
-- Generated rather than filled in by ingest, so the rule lives in exactly one place and every
-- existing row gets it without a backfill. Everything that is not MVK is its own recording.
ALTER TABLE videos
    ADD COLUMN recording_id TEXT GENERATED ALWAYS AS (
        CASE WHEN collection = 'MVK' THEN regexp_replace(video_id, '_[0-9]+$', '') ELSE video_id END
        ) STORED;

CREATE INDEX videos_recording_idx ON videos (recording_id);


-- Where each clip starts within its recording: the summed length of the clips before it. Ordered
-- by video_id, which sorts the zero-padded clip numbers correctly. A gap in the numbering (Jeju
-- 0029 and Okinawa 0020 do not exist) is simply not there to add up, so nothing is mis-shifted.
--
-- A correlated sum rather than a window function: queries join this on video_id, and a filter on
-- video_id cannot be pushed into a window partitioned by recording, so the window version computed
-- every video's offset on every search. This one costs one index lookup per row asked for.
CREATE VIEW video_timeline AS
SELECT v.video_id,
       v.recording_id,
       (SELECT coalesce(sum(p.duration_ms), 0)
        FROM videos p
        WHERE p.recording_id = v.recording_id
          AND p.video_id < v.video_id)::bigint AS offset_ms
FROM videos v;
