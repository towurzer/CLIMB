-- Whisper's own confidence for each segment, and whether the rule in pipeline/asr.py takes it for a
-- hallucination ("Thank you." over regulator noise). Flagged segments stay in the table but are
-- neither embedded nor searched, so a changed rule is `python main.py asr --reclassify`, not a new
-- transcription. Rows from before this migration have no confidence values; the rule judges those
-- by their text and timing alone.

ALTER TABLE transcript_segment
    ADD COLUMN no_speech_prob    REAL,
    ADD COLUMN avg_logprob       REAL,
    ADD COLUMN compression_ratio REAL,
    ADD COLUMN hallucination     BOOLEAN NOT NULL DEFAULT false;
