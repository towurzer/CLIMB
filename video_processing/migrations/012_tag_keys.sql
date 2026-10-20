-- V3C tags as keys that plain query words can be matched against (retrievers.tags), without
-- typing tag:. A key is the tag lowercased, unaccented and with everything that is not a letter or
-- digit dropped, so 'Fish-Eye', 'fish eye' and 'fisheye' are all 'fisheye', and 'Court métrage'
-- is 'courtmetrage'. Query words go through the same function, so both sides always agree.
--
-- Some tags arrive with '?' where the scrape lost a character ('v?lo', 'bodas en espa?a'); their
-- keys keep it, and tag_wildcards holds them as LIKE patterns, '?' matching any one character.
-- Only where enough survived to say something: at least three known characters, and at least two
-- for every '?'. A Korean tag that came through as '????' would otherwise match every four-letter
-- word of every query. Few videos have a wildcard, so a partial index finds them.

CREATE OR REPLACE FUNCTION tag_key(text)
    RETURNS text
    LANGUAGE sql
    IMMUTABLE STRICT PARALLEL SAFE
AS
$$
SELECT regexp_replace(lower(immutable_unaccent($1)), '[^[:alnum:]?]+', '', 'g')
$$;

CREATE OR REPLACE FUNCTION tag_keys(text[])
    RETURNS text[]
    LANGUAGE sql
    IMMUTABLE STRICT PARALLEL SAFE
AS
$$
SELECT coalesce(array_agg(DISTINCT key ORDER BY key), '{}')
FROM (SELECT tag_key(tag) AS key FROM unnest($1) AS tag) keys
WHERE key <> ''
$$;

CREATE OR REPLACE FUNCTION tag_wildcards(text[])
    RETURNS text[]
    LANGUAGE sql
    IMMUTABLE STRICT PARALLEL SAFE
AS
$$
SELECT coalesce(array_agg(replace(key, '?', '_') ORDER BY key), '{}')
FROM unnest(tag_keys($1)) AS key
WHERE key LIKE '%?%'
  AND length(replace(key, '?', '')) >= 3
  AND length(replace(key, '?', '')) >= 2 * (length(key) - length(replace(key, '?', '')))
$$;

ALTER TABLE video_info
    ADD COLUMN tag_keys      TEXT[] GENERATED ALWAYS AS (tag_keys(tags)) STORED,
    ADD COLUMN tag_wildcards TEXT[] GENERATED ALWAYS AS (tag_wildcards(tags)) STORED;

CREATE INDEX video_info_tag_keys_idx ON video_info USING gin (tag_keys);
CREATE INDEX video_info_tag_wildcards_idx ON video_info (video_id) WHERE tag_wildcards <> '{}';
