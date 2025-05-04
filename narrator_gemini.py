import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from typing import List, Optional, Dict
import sqlite3
from datetime import datetime
import json
import logging
from functools import wraps
from retry import retry

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('narrator_gemini')

class NarratorGemini:
    def __init__(self, api_key: str, db_path: str = "stories.db"):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel("gemini-2.0-flash")
        
        self.db_path = db_path
        
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
            
            "character_generator": """Create a detailed character that would fit well into the 
            current story. Include their name, brief physical description, personality, and potential 
            role in the narrative.
            
            Current story context:
            {story_context}
            Genre: {genre}
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

    async def get_story_context(self, story_id: int) -> Dict:
        """Retrieve story context from database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Get story details
            cursor.execute("""
                SELECT title, genre, final_text 
                FROM stories 
                WHERE story_id = ?
            """, (story_id,))
            story_data = cursor.fetchone()
            
            # Get recent contributions
            cursor.execute("""
                SELECT content 
                FROM contributions 
                WHERE story_id = ? 
                ORDER BY timestamp DESC 
                LIMIT 5
            """, (story_id,))
            recent_contributions = cursor.fetchall()
            
            return {
                "title": story_data[0],
                "genre": story_data[1],
                "current_text": story_data[2],
                "recent_contributions": [r[0] for r in recent_contributions]
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
    async def generate_character(self, story_context: Dict) -> str:
        """Generate a new character that fits the story"""
        prompt = self.prompts["character_generator"].format(
            story_context=story_context["current_text"][-500:],
            genre=story_context["genre"]
        )
        
        response = self.model.generate_content(
            generation_config=self.model_config,
            contents=prompt
        )
        
        return response.text.strip()

    @retry(tries=3, delay=2, backoff=2)
    async def generate_plot_twist(self, story_context: Dict) -> str:
        """Generate a plot twist for the current story"""
        prompt = self.prompts["plot_twist"].format(
            story_text=story_context["current_text"][-1000:]
            # TODO: accept list of story characters as context
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