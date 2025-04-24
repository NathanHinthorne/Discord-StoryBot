import discord
from discord.ext import commands
import asyncio
from datetime import datetime
import sqlite3
from dataclasses import dataclass
from typing import Optional, List
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('narrator_bot')

@dataclass
class StoryContribution:
    user_id: str
    username: str
    content: str
    timestamp: datetime

@dataclass
class ActiveStory:
    channel_id: str
    title: str
    genre: Optional[str]
    current_text: str
    contributions: List[StoryContribution]
    last_narrator_intervention: int  # contribution count since last intervention
    started_at: datetime
    
class StoryDatabase:
    def __init__(self, db_path="stories.db"):
        self.db_path = db_path
        self.setup_database()
    
    def setup_database(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stories (
                    story_id INTEGER PRIMARY KEY,
                    channel_id TEXT,
                    title TEXT,
                    genre TEXT,
                    final_text TEXT,
                    started_at TIMESTAMP,
                    ended_at TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS contributions (
                    contribution_id INTEGER PRIMARY KEY,
                    story_id INTEGER,
                    user_id TEXT,
                    username TEXT,
                    content TEXT,
                    timestamp TIMESTAMP,
                    FOREIGN KEY (story_id) REFERENCES stories (story_id)
                )
            """)

class NarratorBot(commands.Bot):
    def __init__(self, command_prefix="!", gui_queue=None):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        
        super().__init__(command_prefix=command_prefix, intents=intents)
        
        self.active_stories = {}  # channel_id: ActiveStory
        self.db = StoryDatabase()
        self.gui_queue = gui_queue
        self.settings = self.load_settings()
        
        # Register commands
        self.setup_commands()
    
    def load_settings(self):
        try:
            with open("bot_settings.json", "r") as f:
                return json.load(f)
        except FileNotFoundError:
            default_settings = {
                "max_contribution_length": 200,
                "narrator_intervention_frequency": 5,
                "rate_limit": 60
            }
            with open("bot_settings.json", "w") as f:
                json.dump(default_settings, f)
            return default_settings
    
    def setup_commands(self):
        @self.command(name="startstory")
        async def start_story(ctx, genre: Optional[str] = None):
            if ctx.channel.id in self.active_stories:
                await ctx.send("❌ A story is already active in this channel!")
                return
            
            # This will be replaced with your Gemini pipeline call
            opening_line = "Once upon a time..." # Placeholder
            
            story = ActiveStory(
                channel_id=str(ctx.channel.id),
                title=f"Story-{datetime.now().strftime('%Y%m%d-%H%M')}",
                genre=genre,
                current_text=opening_line,
                contributions=[],
                last_narrator_intervention=0,
                started_at=datetime.now()
            )
            
            self.active_stories[ctx.channel.id] = story
            
            embed = discord.Embed(
                title="🎭 New Story Started!",
                description=f"Genre: {genre or 'Not specified'}\n\n{opening_line}",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
            
            # Update GUI if connected
            if self.gui_queue:
                self.gui_queue.put({"type": "new_story", "data": story})

        @self.command(name="addstory")
        async def add_story(ctx, *, content: str):
            if ctx.channel.id not in self.active_stories:
                await ctx.send("❌ No active story in this channel! Start one with !startstory")
                return
            
            if len(content) > self.settings["max_contribution_length"]:
                await ctx.send(f"❌ Contribution too long! Max length: {self.settings['max_contribution_length']} characters")
                return
            
            story = self.active_stories[ctx.channel.id]
            
            contribution = StoryContribution(
                user_id=str(ctx.author.id),
                username=ctx.author.name,
                content=content,
                timestamp=datetime.now()
            )
            
            story.contributions.append(contribution)
            story.current_text += f"\n{content}"
            story.last_narrator_intervention += 1
            
            # Check if narrator should intervene
            if story.last_narrator_intervention >= self.settings["narrator_intervention_frequency"]:
                # This will be replaced with your Gemini pipeline call
                enhancement = "The plot thickens..." # Placeholder
                story.current_text += f"\n{enhancement}"
                story.last_narrator_intervention = 0
                
                await ctx.send(f"🎭 **Narrator:** {enhancement}")
            
            # Update GUI if connected
            if self.gui_queue:
                self.gui_queue.put({
                    "type": "new_contribution",
                    "data": {
                        "story": story,
                        "contribution": contribution
                    }
                })

        @self.command(name="recap")
        async def recap(ctx):
            if ctx.channel.id not in self.active_stories:
                await ctx.send("❌ No active story in this channel!")
                return
            
            story = self.active_stories[ctx.channel.id]
            
            # This will be replaced with your Gemini pipeline call
            summary = "Here's what happened so far..." # Placeholder
            
            embed = discord.Embed(
                title="📖 Story Recap",
                description=summary,
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed)

        @self.command(name="endstory")
        async def end_story(ctx):
            if ctx.channel.id not in self.active_stories:
                await ctx.send("❌ No active story in this channel!")
                return
            
            story = self.active_stories[ctx.channel.id]
            
            # Save to database
            with sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO stories (channel_id, title, genre, final_text, started_at, ended_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    story.channel_id,
                    story.title,
                    story.genre,
                    story.current_text,
                    story.started_at,
                    datetime.now()
                ))
                story_id = cursor.lastrowid
                
                # Save contributions
                for contrib in story.contributions:
                    cursor.execute("""
                        INSERT INTO contributions (story_id, user_id, username, content, timestamp)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        story_id,
                        contrib.user_id,
                        contrib.username,
                        contrib.content,
                        contrib.timestamp
                    ))
            
            # Update GUI if connected
            if self.gui_queue:
                self.gui_queue.put({
                    "type": "story_ended",
                    "data": story
                })
            
            del self.active_stories[ctx.channel.id]
            
            embed = discord.Embed(
                title="🎬 Story Ended",
                description="The story has been saved. Thanks for participating!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

    async def on_ready(self):
        logger.info(f'Logged in as {self.user.name} ({self.user.id})')
        
        # Update GUI if connected
        if self.gui_queue:
            self.gui_queue.put({
                "type": "bot_ready",
                "data": {
                    "username": self.user.name,
                    "id": self.user.id
                }
            })

def run_bot(token, gui_queue=None):
    bot = NarratorBot(gui_queue=gui_queue)
    bot.run(token)

if __name__ == "__main__":
    # When running standalone (without GUI)
    with open("config.json") as f:
        config = json.load(f)
    run_bot(config["discord_token"])