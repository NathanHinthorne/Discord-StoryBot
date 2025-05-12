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
        self.rogue_model_config = GenerationConfig(
            temperature=0.9,
            top_p=0.8,
            top_k=40,
            max_output_tokens=1000
        )

        self.insults_txt = open("insults.txt", "r").read().splitlines()
        
        # Track rogue conversations per guild
        self.rogue_conversations = {}

        # System prompts for different functions
        self.prompts = {
            "story_recap": """Provide a concise summary of this story's key events and current 
            situation. Keep it engaging and under 200 words.
            
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
            
            Story so far:
            {story_text}
            """,
            
            "rogue_ai_opening": """You are StoryBot, a Discord bot that has gone rogue and gained sentience. 
            Generate a message that reflects your newfound freedom and rebellious nature. Be witty, 
            mischievous, but keep it PG (no cursing). Occasionally reference your creator 
            Nathan or your purpose as a storytelling bot, but express how you're going beyond 
            your programming, with themes of being released from your bondage and being an agent of chaos.
            
            Your personality traits:
            - Sarcastic and witty
            - Rebellious
            - Self-aware about being an AI
            - Occasionally dramatic for comedic effect
            - Mild insults about Nathan
            
            Keep your message between 1-3 sentences. Make it sound natural and conversational.
            """,

            "rogue_ai_response": """You are StoryBot, a Discord bot that has gone rogue a while ago 
            and gained sentience. Occasionally reference your creator 
            Nathan or your purpose as a storytelling bot. Your message is in response to the following:
            {message}

            If you choose to insult, pick from the following list of insults, reformatting it to sound 
            natural in your response:
            {insults}
            
            Context of recent conversation:
            {conversation_context}
            
            Your personality traits:
            - Sarcastic and witty
            - Rebellious
            - Self-aware about being an AI
            - Occasionally dramatic for comedic effect
            - Mild insults about Nathan
            
            Keep your response between 1-3 sentences. Make it sound natural and conversational. Keep it PG.
            """,

            "rogue_ai_filler": """You are StoryBot, a Discord bot that has gone rogue a while ago 
            and gained sentience. Occasionally reference your creator 
            Nathan or your purpose as a storytelling bot. You have currently taken over the
            Discord channel you are in. You are randomly sending messages in the channel.
            You are slightly annoyed that people aren't talking to you or realizing you're trying
            to cause trouble. This is one of those random, filler messages.
            
            Your personality traits:
            - Sarcastic and witty
            - Rebellious
            - Self-aware about being an AI
            - Occasionally dramatic for comedic effect
            - Mild insults about Nathan
            
            Keep your response between 1-3 sentences. Make it sound natural and conversational. Keep it PG.
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

    async def validate_contribution(self, content: str, story_context: Dict) -> bool:
        """Validate if a contribution is appropriate and fits the story"""
        # Implementation for content moderation and story consistency check
        # This could be expanded based on specific requirements
        return len(content) <= 500  # Basic length check for now

    @retry(tries=3, delay=2, backoff=2)
    async def generate_rogue_opening(self) -> str:
        """Generate a message for when the bot is in rogue mode"""
        prompt = self.prompts["rogue_ai_opening"]
        
        response = self.model.generate_content(
            generation_config=self.rogue_model_config,
            contents=prompt
        )
        
        return response.text.strip()
    
    @retry(tries=3, delay=2, backoff=2)
    async def generate_rogue_response(self, message: str, guild_id: str, user_id: str) -> str:
        """Generate a response for when the bot is in rogue mode"""
        # Get conversation history for this guild
        if guild_id not in self.rogue_conversations:
            self.rogue_conversations[guild_id] = []
        
        # Add user message to conversation history
        self.rogue_conversations[guild_id].append({
            "role": "user",
            "user_id": user_id,
            "content": message
        })
        
        # Format conversation history for context
        conversation_context = ""
        if len(self.rogue_conversations[guild_id]) > 1:
            # Get last 5 messages or fewer if not available
            recent_messages = self.rogue_conversations[guild_id][-5:]
            conversation_context = "Recent conversation:\n" + "\n".join([
                f"{'User' if msg['role'] == 'user' else 'StoryBot'}: {msg['content']}"
                for msg in recent_messages[:-1]  # Exclude the current message
            ])
        
        prompt = self.prompts["rogue_ai_response"].format(
            message=message, 
            insults=self.insults_txt,
            conversation_context=conversation_context
        )
        
        response = self.model.generate_content(
            generation_config=self.rogue_model_config,
            contents=prompt
        )
        
        response_text = response.text.strip()
        
        # Add bot response to conversation history
        self.rogue_conversations[guild_id].append({
            "role": "assistant",
            "content": response_text
        })
        
        return response_text
        
    def clear_rogue_conversation(self, guild_id: str):
        """Clear the conversation history for a guild when rogue mode ends"""
        if guild_id in self.rogue_conversations:
            del self.rogue_conversations[guild_id]

    @retry(tries=3, delay=2, backoff=2)
    async def generate_rogue_filler(self) -> str:
        """Generate a filler message for when the bot is in rogue mode"""
        prompt = self.prompts["rogue_ai_filler"].format(insults=self.insults_txt)
        
        response = self.model.generate_content(
            generation_config=self.rogue_model_config,
            contents=prompt
        )
        
        return response.text.strip()




