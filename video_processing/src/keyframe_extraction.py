import os

import cv2

from config import Config
import custom_logger
from db_queries import DBQueries


def process_video_and_shots(conn, video_id, video_path, scene_file_path):
    """Extracts FPS, saves it, calculates middle frames, and saves JPEGs."""
    conf = Config()
    opencv_logger = custom_logger.get_logger("opencv")
    db_logger = custom_logger.get_logger("postgres_db")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        opencv_logger.error(f"Could not open video {video_path}")
        return

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0 or fps != fps:
        opencv_logger.warn(f"Could not extract FPS from video {video_path}, defaulting to 30")
        fps = 30.0

    # Since FPS might be float
    sample_interval = int(round(fps))

    keyframe_count_total = 0

    with conn.cursor() as cur:
        db_logger.debug(f"Inserting FPS of video ({video_path}) into database")

        cur.execute(DBQueries.insert_FPS, (video_id, fps))

        with open(scene_file_path, 'r') as f:
            lines = f.readlines()

        opencv_logger.info(f"Processing {video_id} (FPS: {fps:.2f}) - {len(lines)} shots found.")

        keyframe_count = 0
        keyframes_dir = os.path.join(conf.DATA_DIR, conf.KEYFRAME_FOLDER)
        os.makedirs(keyframes_dir, exist_ok=True)   # create keyframes directory if it doesn't exist

        for line_num, line in enumerate(lines):
            clean_line = line.strip().replace(',', ' ')
            parts = clean_line.split()

            if len(parts) < 2:
                continue

            start_frame = int(parts[0])
            end_frame = int(parts[1])

            # loop thorugh scene every second (sample interval)
            for target in range(start_frame, end_frame, sample_interval):
                img_filename = f"{video_id}_shot_{line_num:05d}_kf_{keyframe_count:05d}.jpg"
                img_path = os.path.join(keyframes_dir, img_filename)

                if not os.path.exists(img_path):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                    ret, frame = cap.read()

                    # fallback: if the frame is not read correctly, try reading sequentially
                    if not ret:
                        opencv_logger.warn(f"Could not read frame {target} in {video_id}, trying sequential read")
                        
                        
                        # go back to the start of the shot
                        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
                        for current_frame in range (start_frame, target + 1):
                            ret, frame = cap.read()
                            if not ret:
                                # video absolutely broken --> skip
                                opencv_logger.warn(f"Could not read frame {current_frame} in {video_id} during sequential read")
                                break

                    if ret:
                        cv2.imwrite(img_path, frame)

                    else:
                        # only skip if fallback also fails
                        opencv_logger.warn(f"Could not read frame {target} in {video_id}")
                        continue

                db_logger.debug(f"Inserting keyframe {target} of shot {line_num} for video {video_id} into database")
                cur.execute(DBQueries.insert_shot_metadata, (video_id, start_frame, end_frame, target, img_path))

                keyframe_count += 1
            
            keyframe_count_total += keyframe_count

    conn.commit()
    cap.release()
    db_logger.info(f"Finished processing {video_id}. Total keyframes extracted: {keyframe_count_total}")