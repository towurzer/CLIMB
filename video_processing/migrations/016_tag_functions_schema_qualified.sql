-- pg_restore runs with an empty search_path, so a function body that calls another of our functions
-- without its schema finds nothing there. 012's tag functions did: restoring a dump failed at
-- CREATE TABLE video_info, whose generated tag_keys column calls tag_keys(), which calls tag_key(),
-- which calls immutable_unaccent(). Same bodies with every call schema-qualified, the way 001's
-- immutable_unaccent already is. The results do not change, so the stored columns stay valid.

CREATE OR REPLACE FUNCTION tag_key(text)
    RETURNS text
    LANGUAGE sql
    IMMUTABLE STRICT PARALLEL SAFE
AS
$$
SELECT regexp_replace(lower(public.immutable_unaccent($1)), '[^[:alnum:]?]+', '', 'g')
$$;

CREATE OR REPLACE FUNCTION tag_keys(text[])
    RETURNS text[]
    LANGUAGE sql
    IMMUTABLE STRICT PARALLEL SAFE
AS
$$
SELECT coalesce(array_agg(DISTINCT key ORDER BY key), '{}')
FROM (SELECT public.tag_key(tag) AS key FROM unnest($1) AS tag) keys
WHERE key <> ''
$$;

CREATE OR REPLACE FUNCTION tag_wildcards(text[])
    RETURNS text[]
    LANGUAGE sql
    IMMUTABLE STRICT PARALLEL SAFE
AS
$$
SELECT coalesce(array_agg(replace(key, '?', '_') ORDER BY key), '{}')
FROM unnest(public.tag_keys($1)) AS key
WHERE key LIKE '%?%'
  AND length(replace(key, '?', '')) >= 3
  AND length(replace(key, '?', '')) >= 2 * (length(key) - length(replace(key, '?', '')))
$$;
