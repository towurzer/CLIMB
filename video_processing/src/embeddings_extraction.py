import torch
from transformers import AutoProcessor, AutoModel
from PIL import Image
import psycopg2

import custom_logger
from db_queries import DBQueries
from db_setup import connect_to_database

from config import Config

def extract_and_store_embeddings():
    """
    Scans the database for video shots missing embeddings, extracts features
    using SigLIP 2 on the GPU, and writes the vectors back in optimized batches.
    """
    # Load configuration settings
    config = Config()

    # Initializing the logger for this module
    logger = custom_logger.get_logger("embeddings")
    
    # Check for GPU availability
    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(f"Initializing {config.MODEL_NAME} on device: {device}")
    
    # Attempt to load the model and processor
    try:
        processor = AutoProcessor.from_pretrained(config.MODEL_NAME)
        model = AutoModel.from_pretrained(config.MODEL_NAME).to(device)
        model.eval()  # Switch model to evaluation mode (disables dropout/batchnorm training behavior)

    except Exception as e:
        logger.error(f"Failed to load model {config.MODEL_NAME}: {e}")
        return

    conn = connect_to_database()
    if not conn:
        logger.error("Database connection initialization failed. Aborting pipeline.")
        return

    # cursor used for all database interactions
    cur = conn.cursor()

    # query for all shots that do not have embeddings yet 
    try:
        cur.execute(DBQueries.get_pending_embeddings)
        all_rows = cur.fetchall()

    except Exception as e:
        logger.error(f"Failed to execute pending embeddings query: {e}")
        cur.close()
        conn.close()
        return

    if not all_rows:
        logger.info("No shots require embedding calculations. Database is up to date!")
        cur.close()
        conn.close()
        return

    logger.info(f"Found {len(all_rows)} shots requiring vector embedding computation.")
    
    # process in batches to optimize GPU usage and database commits
    for i in range(0, len(all_rows), config.EMBEDDING_BATCH_SIZE):
        batch = all_rows[i : i + config.EMBEDDING_BATCH_SIZE]
        shot_ids = [row[0] for row in batch]
        img_paths = [row[1] for row in batch]
        
        valid_images = []
        valid_shot_ids = []
        
        # Safely open extracted keyframe image files
        for s_id, path in zip(shot_ids, img_paths):
            try:
                img = Image.open(path).convert("RGB")
                valid_images.append(img)
                valid_shot_ids.append(s_id)
                
            except Exception as e:
                logger.warning(f"Skipping missing or corrupt keyframe image file [{path}]: {e}")
                
        if not valid_images:
            continue
            
        try:
            inputs = processor(images=valid_images, return_tensors="pt").to(device)            
            
            with torch.no_grad():
                with torch.amp.autocast(device_type="cuda" if "cuda" in device else "cpu", dtype=torch.float16):
                    # Call the internal vision_model directly to completely bypass the text layer requirement
                    model_output = model.vision_model(**inputs)
                    
                    # Grab the pooled output (this is the global image embedding vector)
                    image_features = model_output.pooler_output
            
                    # L2 Normalize
                    image_features = image_features / image_features.norm(p=2, dim=-1, keepdim=True)
            
            embeddings_list = image_features.cpu().numpy().tolist()
            
            # Update the database with new embeddings in a single batch operation for efficiency
            update_payload = [(emb, s_id) for emb, s_id in zip(embeddings_list, valid_shot_ids)]
            cur.executemany(DBQueries.update_shot_embedding, update_payload)
            conn.commit()
            
            logger.debug(f"Successfully committed batch: Shot IDs {valid_shot_ids[0]} to {valid_shot_ids[-1]}")
            
        except Exception as batch_error:
            logger.error(f"Batch execution failure processing range {i} to {i+config.EMBEDDING_BATCH_SIZE}: {batch_error}")
            conn.rollback()
            
    cur.close()
    conn.close()
    logger.info("All pending target video keyframe embeddings generated and stored successfully!")