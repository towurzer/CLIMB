import torch
from transformers import AutoProcessor, AutoModel
import psycopg2

import custom_logger
from db_queries import DBQueries
from db_setup import connect_to_database

from config import Config

import time
from dotenv import load_dotenv
from PIL import Image
import matplotlib.pyplot as plt
import os

class SearchEngine:
    def __init__(self, config: Config, db_connection):
        self.logger = custom_logger.get_logger("search_engine")
        self.config = config
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.db_conn = db_connection
        self.fps_cache = {}  # Cache for video FPS values to avoid repeated DB queries

    
        self.logger.info(f"Initializing {config.MODEL_NAME} on device: {self.device}")
    
        # Attempt to load the model and processor
        try:
            self.processor = AutoProcessor.from_pretrained(config.MODEL_NAME)
            self.model = AutoModel.from_pretrained(config.MODEL_NAME).to(self.   device)
            self.model.eval()  # Switch model to evaluation mode (disables dropout/batchnorm training behavior)

        except Exception as e:
            self.logger.error(f"Failed to load model {config.MODEL_NAME}: {e}")

        self._load_video_metadata()

    def _load_video_metadata(self):
        """
        Loads video metadata from the database into memory for quick access.
        """
        try:
            with self.db_conn.cursor() as cur:
                cur.execute(DBQueries.fetch_video_metadata)
                self.fps_cache = {row[0]: row[1] for row in cur.fetchall()}

            self.logger.info(f"Loaded metadata for {len(self.fps_cache)} videos into cache.")

        except Exception as e:
            self.logger.error(f"Failed to load video metadata: {e}")
            self.fps_cache = {} # Reset cache on failure

    def get_text_vector(self, prompt: str) -> list:
        """
        Converts a text string into a 1024-dimensional list.
        """
        inputs = self.processor(text=[prompt], return_tensors="pt", padding="max_length", truncation=True).to(self.device)
        
        with torch.no_grad():
            outputs = self.model.get_text_features(**inputs)
            
            if hasattr(outputs, 'text_embeds'):
                text_features = outputs.text_embeds

            elif hasattr(outputs, 'pooler_output'):
                text_features = outputs.pooler_output

            else:
                # If it's a 3D tensor (batch, seq_len, dim), extract the first token (CLS)
                text_features = outputs[0]
                if len(text_features.shape) == 3:
                    text_features = text_features[:, 0, :]

            # L2 normalize the 1024-dimensional vector
            text_features = text_features / text_features.norm(p=2, dim=-1, keepdim=True)
            
        # Flatten it to a clean 1-D list of exactly 1024 numbers
        return text_features.cpu().numpy().flatten().tolist()

    def search(self,prompt: str) -> list:
        """
        Takes a text prompt, converts it to a vector, and performs a cosine similarity search in the database.
        Returns a list of shots with their metadata and similarity scores.  
        """
        text_vector = self.get_text_vector(prompt)
        
        with self.db_conn.cursor() as cur:
            try:
                cur.execute(DBQueries.perform_cosine_similarity_query, (text_vector, self.config.SEARCH_TOP_K))
                results = cur.fetchall()
                return results

            except Exception as e:
                self.logger.error(f"Failed to execute search query: {e}")
                return []

    def enrich_results(self, raw_results: list) -> dict:
        """
        Enriches the raw search results with additional metadata, such as FPS and put it into a clean dictionary.
        """
        enriched_results = []

        for shot in raw_results:
            # get the FPS for the video_id from the cache, defaulting to 25.0 if not found
            fps = self.fps_cache.get(shot[1], 25.0)
            start_frame_time = shot[2] / fps * 1000  # Calculate the start frame time in milliseconds 
            end_frame_time = shot[3] / fps * 1000  # Calculate the end frame time in milliseconds 
            middle_frame_time = shot[4] / fps * 1000  # Calculate the middle frame time in milliseconds   

            enriched_results.append({
                "shot_id": shot[0],
                "video_id": shot[1],
                "start_frame": shot[2],
                "end_frame": shot[3],
                "middle_frame": shot[4],
                "image_path": shot[5],
                "similarity_score": shot[6],
                "fps": fps,
                "start_frame_time_ms": start_frame_time,
                "end_frame_time_ms": end_frame_time,
                "middle_frame_time_ms": middle_frame_time
            })

        return enriched_results

    def visualize_results(self, search_results, columns=6):
        """
        Visualizes the search results in a grid format. 
        Will not be used in the final implementation, but is useful for debugging and development.
        """
        
        # Filter for shots where the image actually exists first
        valid_shots = [shot for shot in search_results if os.path.exists(shot["image_path"])]
        
        if not valid_shots:
            print("No valid images found to display.")
            return

        num_images = len(valid_shots)
        # Calculate required rows dynamically based on the number of valid images
        rows = (num_images + columns - 1) // columns 

        # Create the matplotlib figure
        fig, axes = plt.subplots(rows, columns, figsize=(16, 3.5 * rows))
        
        # Flatten the axes array to make indexing easy (in case it's 2D)
        axes = axes.flatten()

        for i in range(len(axes)):
            if i < num_images:
                shot = enriched_results[i]
                img = Image.open(shot["image_path"])
                axes[i].imshow(img)
                
                axes[i].set_title(f"VID: {shot['video_id']} | SHOT: {shot['shot_id']} | SIM: {shot['similarity_score']:.3f}", fontsize=8, fontweight='bold')
        
            axes[i].axis('off')

        plt.tight_layout()
        plt.show()

# Example usage:
if __name__ == "__main__":
    load_dotenv()  # Load environment variables from .env file
    conn = connect_to_database()
    if not conn:
        print("Database connection initialization failed. Cannot perform search.")

    else:
        search_engine = SearchEngine(Config(), conn)

        while True:
            try:
                prompt = input("Enter your search prompt (or type 'exit' to quit): ")
                if prompt.lower() == 'exit':
                    break

                start_time = time.monotonic()  # Start timing the search
                search_results = search_engine.search(prompt)
                elapsed_time = time.monotonic() - start_time
                print(f"Search completed in {elapsed_time:.2f} seconds. Found {len(search_results)} results.")

                start_time = time.monotonic()  # Start timing the enrichment
                enriched_results = search_engine.enrich_results(search_results)
                elapsed_time = time.monotonic() - start_time
                print(f"Enrichment completed in {elapsed_time:.2f} seconds.")

                # print the enriched results in a readable format
                for shot in enriched_results:
                    print(f"Shot ID: {shot['shot_id']}, Video ID: {shot['video_id']}, "
                          f"Start Frame: {shot['start_frame']} ({shot['start_frame_time_ms']:.2f} ms), "
                          f"Middle Frame: {shot['middle_frame']} ({shot['middle_frame_time_ms']:.2f} ms), "
                          f"End Frame: {shot['end_frame']} ({shot['end_frame_time_ms']:.2f} ms), "
                          f"Similarity Score: {shot['similarity_score']:.3f}, "
                          f"Image Path: {shot['image_path']}")

                search_engine.visualize_results(enriched_results)

            except KeyboardInterrupt:
                print("\nExiting search engine.")
                break

        conn.close()
    