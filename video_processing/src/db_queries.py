from dataclasses import dataclass


@dataclass
class DBQueries:
    create_video_table = """
                         CREATE TABLE IF NOT EXISTS videos
                         (
                             video_id
                             VARCHAR
                         (
                             100
                         ) UNIQUE PRIMARY KEY,
                             fps FLOAT NOT NULL
                             ); \
                         """

    create_shots_table = """CREATE TABLE IF NOT EXISTS shots
    (
        shot_id
        SERIAL
        PRIMARY
        KEY,
        video_id
        VARCHAR
                            (
        100
                            ) REFERENCES videos
                            (
                                video_id
                            ),
        start_frame INT NOT NULL,
        end_frame INT NOT NULL,
        middle_frame INT NOT NULL,
        image_path TEXT UNIQUE NOT NULL ,
        embedding vector(1024)
        );"""

    insert_FPS = """
                 INSERT INTO videos (video_id, fps)
                 VALUES (%s, %s) ON CONFLICT (video_id) DO NOTHING; \
                 """

    insert_shot_metadata = """
                           INSERT INTO shots (video_id, start_frame, end_frame, middle_frame, image_path)
                           VALUES (%s, %s, %s, %s, %s) ON CONFLICT (image_path) DO NOTHING; \
                           """

    get_pending_embeddings = """
                             SELECT shot_id, image_path 
                             FROM shots 
                             WHERE embedding IS NULL;
                             """

    update_shot_embedding = """
                            UPDATE shots 
                            SET embedding = %s 
                            WHERE shot_id = %s;
                            """

    perform_cosine_similarity_query = """
                                    SELECT shot_id, video_id, start_frame, end_frame, middle_frame, image_path, 1 - (embedding <=> %s::vector) AS similarity
                                    FROM shots
                                    ORDER BY similarity DESC
                                    LIMIT %s;   
                                    """

    fetch_video_metadata = """
                            SELECT video_id, fps
                            FROM videos;
                            """