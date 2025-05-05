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

        # System prompts for different functions
        self.prompts = {
            "narrator_intervention": """You are a skilled story narrator. Review the current story 
            and provide a brief narrative intervention (2-3 sentences) that helps transition between 
            the previous contributions while maintaining the story's tone and advancing the plot.
            
            Current story:
            {current_story}
            
            Last few contributions:
            {recent_contributions}
            """,
            
            "story_recap": """Provide a concise summary of this story's key events and current 
            situation. Keep it engaging and under 200 words.
            
            Story so far:
            {story_text}
            """,
            
            "plot_twist": """Generate an unexpected but coherent plot twist that could be 
            introduced into the current story. Make it surprising but consistent with the 
            established narrative. Keep it under 300 words and ensure it's 2 paragraphs. 
            Do not include information about why the twist happens.
            
            Story so far:
            {story_text}
            Genre: {genre}
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
    async def generate_narrator_intervention(self, story_context: Dict) -> str:
        """Generate a narrator's intervention based on recent contributions"""
        prompt = self.prompts["narrator_intervention"].format(
            current_story=story_context["current_text"][-1000:],  # Last 1000 chars for context
            recent_contributions="\n".join(story_context["recent_contributions"])
        )
        
        response = self.model.generate_content(
            generation_config=self.model_config,
            contents=prompt
        )
        
        return response.text.strip()

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
            genre=story_context.get("genre", "fiction")
        )
        
        response = self.model.generate_content(
            generation_config=self.model_config,
            contents=prompt
        )
        
        return response.text.strip()

    async def validate_contribution(self, content: str, story_context: Dict) -> bool:
        """Validate if a contribution is appropriate and fits the story"""
        # Implementation for content moderation and story consistency check
        # This could be expanded based on specific requirements
        return len(content) <= 500  # Basic length check for now
