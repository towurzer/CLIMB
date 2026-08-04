from dataclasses import dataclass


@dataclass
class DBQueries:
    """
    Runtime queries only. Schema DDL lives in video_processing/migrations/ and is applied by
    db/migrate.py
     ANN and GIN indexes are built separately from the tables.
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

    perform_cosine_similarity_query_with_exclude = """
                                    SELECT shot_id, video_id, start_frame, end_frame, middle_frame, image_path, 1 - (embedding <=> %s::vector) AS similarity
                                    FROM shots
                                    WHERE video_id != ALL(%s)
                                    ORDER BY similarity DESC
                                    LIMIT %s;   
                                    """

    fetch_video_metadata = """
                            SELECT video_id, fps
                            FROM videos;
                            """