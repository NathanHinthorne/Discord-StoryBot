import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from typing import List, Optional, Dict
import logging
from functools import wraps
from retry import retry
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('narrator_gemini')

class NarratorGemini:
    def __init__(self, api_key: str, firebase_db=None):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel("gemini-2.0-flash")
        
        # Use the provided Firebase DB instance or create a new connection
        if firebase_db:
            self.db = firebase_db
        else:
            # This assumes Firebase has already been initialized in the main app
            self.db = firestore.client()
        
        # Load model configurations
        self.model_config = GenerationConfig(
            temperature=0.7,
            top_p=0.8,
            top_k=40,
            max_output_tokens=1000
        )

        # high temp model
        self.high_temp_model_config = GenerationConfig(
            temperature=0.9,
            top_p=0.8,
            top_k=40,
            max_output_tokens=1000
        )

        # System prompts for different functions
        self.prompts = {
            "story_recap": """Provide a concise summary of this story's key events and current 
            situation. Keep it engaging and under 200 words.

            If there is nothing to go on, do not generate a recap. Instead, simply say "Well... that was uneventful."
            
            Story so far:
            {story_text}
            """,
            
            "plot_twist": """Generate an unexpected but coherent plot twist that could be 
            introduced into the current story. From a scale of 1 to 5, with 1 being the least 
            surprising and 5 being the most, generate a twist of intensity {intensity}. 

            If a prompt or theme is provided, the plot twist should incorporate that 
            prompt/theme in some way. If no prompt is provided, generate a plot twist 
            that fits the story's genre and established narrative.

            Prompt/theme: {prompt}
            
            Make it consistent with the established narrative. Keep it under 300 words and 
            ensure it's 2 paragraphs. Do not include information about why the twist happens.
            Do not include information related to pagan rituals or practices.

            If there is nothing to go on, do not generate a plot twist. Instead, return an empty string.
            
            Story so far:
            {story_text}
            """
        }

    async def get_story_context(self, story_id: str) -> Dict:
        """Retrieve story context from Firestore"""
        # Get story details
        story_doc = self.db.collection('stories').document(story_id).get()
        if not story_doc.exists:
            logger.error(f"Story {story_id} not found")
            return {}
            
        story_data = story_doc.to_dict()
        
        # Get recent contributions
        contributions = self.db.collection('contributions')\
            .where('story_id', '==', story_id)\
            .order_by('timestamp', direction=firestore.Query.DESCENDING)\
            .limit(5)\
            .stream()
            
        recent_contributions = [doc.to_dict()['content'] for doc in contributions]
        
        return {
            "title": story_data.get('title', ''),
            "genre": story_data.get('genre', 'fiction'),  # Default to fiction if not specified
            "current_text": story_data.get('final_text', ''),
            "recent_contributions": recent_contributions
        }

    @retry(tries=3, delay=2, backoff=2)
    async def generate_story_recap(self, story_text: str) -> str:
        """Generate a recap of the story so far"""
        prompt = self.prompts["story_recap"].format(story_text=story_text)
        
        response = self.model.generate_content(
            generation_config=self.model_config,
            contents=prompt
        )
        
        return response.text.strip()

    @retry(tries=3, delay=2, backoff=2)
    async def generate_plot_twist(self, story_context: Dict) -> str:
        """Generate a plot twist for the current story"""
        prompt = self.prompts["plot_twist"].format(
            story_text=story_context["current_text"][-1000:],
            intensity=story_context["intensity"],
            prompt=story_context["prompt"]
        )
        
        response = self.model.generate_content(
            generation_config=self.model_config,
            contents=prompt
        )
        
        return response.text.strip()
    