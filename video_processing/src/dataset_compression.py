import os
import subprocess
from pathlib import Path

import utils
from config import Config
import custom_logger

from concurrent.futures import ProcessPoolExecutor, as_completed
import time
import multiprocessing

file_lock = multiprocessing.Lock()  # To synchronize access to the checkpoint file across processes

def transcode_worker(args):
    '''Worker function for transcoding a single video. This is designed to be called in parallel.'''
    video_path, output_path, use_gpu = args

    output_filename = video_path.stem + Config.WEB_VIDEO_EXTENSION
    output_path_i = Path(output_path) / output_filename

    return video_path, transcode_video(video_path, output_path_i, use_gpu)

def choose_workers(use_gpu: bool) -> int:
    '''Determines the number of worker processes to use based on system capabilities and whether GPU encoding is enabled.'''
    cpu = os.cpu_count() or 4

    if use_gpu:
        return 3 if cpu >= 6 else 2
    else:
        return max(1, cpu // 2)

def transcode_video(input_path, output_path, use_gpu=False) -> bool:
    """
    Transcodes a single video to H.264 MP4, scaled to the target resolution,
    and optimized for instant web playback.
    """
    # The FFmpeg command
    if use_gpu:
        command = [
            "ffmpeg",
            "-y",  # Overwrite output files without asking
            "-hwaccel", "cuda", # Use CUDA for hardware acceleration (GPU encoding)
            "-hwaccel_output_format", "cuda",   # Process video in GPU memory to avoid unnecessary transfers
            "-i", str(input_path),  # Input file path
            "-vf", f"scale_cuda=-2:{Config.WEB_RESOLUTION}",    # Scale height to web_resolution using GPU, width auto-calculated to keep aspect ratio
            "-c:v", "h264_nvenc",   # Use NVENC H.264 video codec (GPU-accelerated)
            "-preset", "p4",    # NVENC preset for good quality/speed balance (p1=slowest/best quality, p7=fastest/worst quality)
            "-rc", "vbr",   # Use Variable Bitrate mode for better quality at lower bitrates
            "-cq", "28",    # Constant Quantization Parameter (Quality). 28 is a good starting point for web videos, adjust as needed.
            "-c:a", "aac",  # Use AAC audio codec
            "-b:a", "128k", # Audio bitrate
            "-movflags", "+faststart",  # Moves metadata to the start so web browsers play instantly
            str(output_path)    # Output file path
        ]

    else:
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
        logger = custom_logger.get_logger("FFmpeg")
        logger.error(f"Failed to transcode {input_path.name}")
        logger.error(e.stderr.decode('utf-8', errors='ignore'))
        return False

def has_nvenc() -> bool:
    '''Checks if the system has NVENC support for hardware-accelerated video encoding. (GPU encoding)'''
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True
        )
        return "h264_nvenc" in result.stdout

    except Exception:
        return False

def compress() -> None:
    """
    Compresses the Dataset to a Web Ready format
    """
    logger = custom_logger.get_logger("compression")
    input_path = os.path.join(Config.DATA_DIR, Config.DATASET_FOLDER)
    output_path = os.path.join(Config.DATA_DIR, Config.WEB_READY_DATASET_FOLDER)

    utils.create_dir(output_path)
    utils.create_dir(Config.LOG_FOLDER)

    logger.info("Checking GPU support...")
    use_gpu = has_nvenc()

    if use_gpu:
        logger.info("GPU encoding enabled (NVENC detected)")

    else:
        logger.info("GPU not available, falling back to CPU")

    videos = [f for f in Path(input_path).iterdir() if f.suffix.lower() in Config.VIDEO_EXTENSIONS]
    completed = load_completed()
    if not videos:
        # FileNotFoundError(f"No videos found in {input_path}. Please check the path.")
        logger.warn(f"No videos found in {input_path}. Please check the path.")
        return

    logger.info(f"Started transcoding {len(videos)} videos (This might take a while...)")
    successful = 0

    # filter out already completed videos
    todo_videos = [v for v in videos if f"{input_path}/{v.name}" not in completed]

    logger.info(f"{len(todo_videos)} videos left to transcode after filtering out already completed ones")

    if Config.COMPRESSION_PARALLEL:
        workers = choose_workers(use_gpu)

        logger.info(f"Using {workers} workers")

        args = [(v, output_path, use_gpu) for v in todo_videos]

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(transcode_worker, a): a[0]
                for a in args
            }

            for future in as_completed(futures):
                vp = futures[future]

                try:
                    vp, success = future.result()

                    output_filename = vp.stem + Config.WEB_VIDEO_EXTENSION
                    output_path_i = Path(output_path) / output_filename

                    if success:
                        mark_completed(f"{input_path}/{vp.name}")
                        successful += 1

                        logger.info(f"Successfully transcoded  {vp.resolve()} -> {output_path_i.resolve()}")
                    else:
                        logger.warning(f"FAILED transcoding {vp.resolve()} -> {output_path_i.resolve()}")
                                        

                except Exception as e:
                    logger.error(f"Crash on {vp.name}: {e}")
    
    else:
        for i, video_path in enumerate(todo_videos, 1):
            output_filename = video_path.stem + Config.WEB_VIDEO_EXTENSION
            output_path_i = Path(output_path) / output_filename

            #if f"{input_path}/{video_path.name}" in completed:
            #    logger.info(f"{input_path}/{video_path.name} already transcoded, skipping")
            #    successful += 1
            #    continue

            # print(f"[{i}/{len(videos)}] Transcoding: {input_path}/{video_path.name} -> {output_path}/{output_filename}...", end="", flush=True)
            # changed to add the console output stream to the logger

            success = transcode_video(video_path, output_path_i, use_gpu=use_gpu)

            if success:
                mark_completed(f"{input_path}/{video_path.name}")
                logger.info(f"Successfully transcoded {input_path}/{video_path.name} -> {output_path}/{output_filename}")
                # print(" DONE")
                successful += 1
            else:
                # print("FAILED")
                logger.warn(f"FAILED transcoding {input_path}/{video_path.name} -> {output_path}/{output_filename}")

    logger.info(f"Finished transcoding! Successfully transcoded {successful} out of {len(todo_videos)} videos.")


# Compression logger
def load_completed() -> set[str]:
    path = Path(os.path.join(Config.DATA_DIR, Config.COMPRESSION_CHECKPOINT_FILE))
    if not path.exists():
        return set()

    with open(os.path.join(Config.DATA_DIR, Config.COMPRESSION_CHECKPOINT_FILE), "r", encoding="utf-8") as f:
        return {
            line.strip()
            for line in f
            if line.strip()
        }


def mark_completed(filename: str) -> None:
    with file_lock:
        with open(os.path.join(Config.DATA_DIR, Config.COMPRESSION_CHECKPOINT_FILE), "a", encoding="utf-8") as f:
            f.write(f"{filename}\n")