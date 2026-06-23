import torch
from transformers import Blip2Processor, Blip2ForConditionalGeneration, BitsAndBytesConfig
import custom_logger
from config import Config
from PIL import Image
import os
import gc


class VQAEngine:
    def __init__(self, config: Config):
        self.logger = custom_logger.get_logger("vqa_engine")
        self.config = config
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        self.logger.info(f"Initializing {config.VQA_MODEL_NAME} on device: {self.device}")

        if self.device == "cuda":
            torch.cuda.empty_cache()
            gc.collect()
        
        try:
            self.processor = Blip2Processor.from_pretrained(config.VQA_MODEL_NAME)
            
            if self.device == "cuda":
                self.logger.info("Configuring BLIP model for GPU ...")
                
                # Limit VRAM for BLIP-2 to 7GB, so SigLIP 2 has space
                max_memory_mapping = {0: "7GiB"}
                quantization_config = BitsAndBytesConfig(load_in_8bit=True)
                
                self.model = Blip2ForConditionalGeneration.from_pretrained(
                    config.VQA_MODEL_NAME,
                    quantization_config=quantization_config,
                    device_map="auto",
                    max_memory=max_memory_mapping,
                    low_cpu_mem_usage=True  # avoids loading the full model into CPU memory before quantization, which can cause OOM errors
                )
                
            else:
                self.logger.info("Loading BLIP model on CPU with 32-bit precision ...")
                # If CUDA is not available, load the model with 32 float precision as standard
                self.model = Blip2ForConditionalGeneration.from_pretrained(
                    config.VQA_MODEL_NAME,
                    torch_dtype=torch.float32
                ).to(self.device)

        except Exception as e:
            self.logger.error(f"Failed to load model {config.VQA_MODEL_NAME}: {e}")
            
    def answer_question(self, image_path: str, question: str) -> str:
        """
        Answers a question about the content of an image.
        """
        if not os.path.exists(image_path):
            self.logger.error(f"Keyframe image not found at path: {image_path}")
            return "Error: Image not found."

        try:
            image = Image.open(image_path).convert("RGB")

            # prompt engineering for BLIP-2
            prompt = f"Question: {question} Answer:"

            inputs = self.processor(image, prompt, return_tensors="pt").to(self.device)

            with torch.no_grad():
                output = self.model.generate(**inputs, max_new_tokens=50)

            answer = self.processor.decode(output[0], skip_special_tokens=True)
            return answer
    
        except Exception as e:
            self.logger.error(f"Error during VQA processing: {e}")
            return "Error: Failed to process the question."


# Example usage:
if __name__ == "__main__":
    config = Config()
    vqa_engine = VQAEngine(config)

    # Example question about a keyframe image
    keyframe_image_path = "/home/sebastian/Uni/IVADL/CLIMB/dataset/keyframes/00017_shot_00017_kf_00099.jpg"
    question = "What objects are visible in this image?"
    answer = vqa_engine.answer_question(keyframe_image_path, question)
    print(f"Question: {question}\nAnswer: {answer}")