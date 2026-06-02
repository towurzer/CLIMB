import os
import subprocess
from pathlib import Path

import utils
from config import Config


def transcode_video(input_path, output_path):
    """
    Transcodes a single video to H.264 MP4, scaled to the target resolution,
    and optimized for instant web playback.
    """
    # The FFmpeg command
    command = [
        "ffmpeg",
        "-y",  # Overwrite output files without asking
        "-i", str(input_path),  # Input file path
        "-vf", f"scale=-2:{Config.WEB_RESOLUTION}",  # Scale height to web_resolution, width auto-calculated to keep aspect ratio
        "-c:v", "libx264",  # Use H.264 video codec (Standard for web)
        "-preset", "fast",  # Encoding speed (fast keeps a good balance of speed/quality)
        "-crf", "28",  # Constant Rate Factor (Quality). 28 makes file size very small.
        "-c:a", "aac",  # Use AAC audio codec
        "-b:a", "128k",  # Audio bitrate
        "-movflags", "+faststart",  # Moves metadata to the start so web browsers play instantly
        str(output_path)  # Output file path
    ]

    try:
        # stdout=DEVNULL -> do not spam the console
        # stderr=PIPE -> spam console if error
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] Failed to transcode {input_path.name}")
        print(e.stderr.decode('utf-8', errors='ignore'))
        return False


def compress() -> None:
    """
    Compresses the Dataset to a Web Ready format
    """
    input_path = os.path.join(Config.DATA_DIR, Config.DATASET_FOLDER)
    output_path = os.path.join(Config.DATA_DIR, Config.WEB_READY_DATASET_FOLDER)

    utils.create_dir(output_path)

    videos = [f for f in Path(input_path).iterdir() if f.suffix.lower() in Config.VIDEO_EXTENSIONS]
    if not videos:
        FileNotFoundError(f"No videos found in {input_path}. Please check the path.")
        return


    print(f"Found {len(videos)} videos to transcode. This might take a while...\n")
    successful = 0
    for i, video_path in enumerate(videos, 1):
        output_filename = video_path.stem + Config.WEB_VIDEO_EXTENSION
        output_path_i = Path(output_path) / output_filename

        print(f"[{i}/{len(videos)}] Transcoding: {input_path}/{video_path.name} -> {output_path}/{output_filename}...", end="", flush=True)

        success = transcode_video(video_path, output_path_i)

        if success:
            print(" DONE")
            successful += 1
        else:
            print("FAILED")

    print(f"\nFinished! Successfully transcoded {successful} out of {len(videos)} videos.")


if __name__ == "__main__":
    compress()