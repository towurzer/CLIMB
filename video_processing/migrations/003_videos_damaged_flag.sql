-- Mark videos whose bitstream does not fully decode.
--
-- The V3C1_200 course set contains two of these (00016 and 00024). Their containers claim 23,120
-- and 47,289 frames, but the H.264 streams are damaged -- ffmpeg reports thousands of
-- "Invalid NAL unit size" and "Error splitting the input into NAL units" errors and delivers
-- roughly 73% of the advertised frames. Full V3C will contain more.
--
-- These videos are still indexed, because three quarters of a video is a lot of searchable
-- material to discard and a KIS target may well sit in the good part.

ALTER TABLE videos
    ADD COLUMN damaged BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN videos.damaged IS
    'Decoder reported errors and delivered fewer frames than the container advertised. The video '
        'is indexed from whatever decoded successfully.';
