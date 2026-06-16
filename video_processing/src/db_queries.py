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
                         ) PRIMARY KEY,
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
        image_path TEXT NOT NULL
        );"""

    insert_FPS = """
                 INSERT INTO videos (video_id, fps)
                 VALUES (%s, %s) ON CONFLICT (video_id) DO NOTHING; \
                 """

    insert_shot_metadata = """
                           INSERT INTO shots (video_id, start_frame, end_frame, middle_frame, image_path)
                           VALUES (%s, %s, %s, %s, %s); \
                           """
