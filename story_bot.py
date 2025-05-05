import random
import discord
from discord.ext import commands
import asyncio
from datetime import datetime
import sqlite3
from dataclasses import dataclass
from typing import Optional, List
import json
import logging
from discord import app_commands
import os
from dotenv import load_dotenv

# Import custom modules
from narrator_gemini import NarratorGemini
from google_docs_exporter import GoogleDocsExporter
from firebase_db import FirebaseDatabase
import webserver

load_dotenv() #? move to init?

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('story_bot')

@dataclass
class StoryContribution:
    user_id: str
    username: str
    display_name: str
    content: str
    timestamp: datetime

@dataclass
class ActiveStory:
    channel_id: str
    title: str
    opening_text: str
    current_text: str
    contributions: List[StoryContribution]
    last_narrator_intervention: int  # contribution count since last intervention
    started_at: datetime

class StoryBot(commands.Bot):
    def __init__(self, command_prefix="/", gui_queue=None):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        
        # Initialize with slash commands support
        super().__init__(
            command_prefix=command_prefix,
            intents=intents,
            help_command=None
        )
        
        self.active_stories = {}  # channel_id: ActiveStory
        self.db = FirebaseDatabase()  # Use Firebase instead of SQLite
        self.gui_queue = gui_queue
        self.settings = self.load_settings()
        self.designated_channels = self.load_designated_channels()
        self.pending_exports = {}  # For tracking export reactions

        # personality parameters
        self.deny_request_percentage = 0.15 # 15% chance to deny a request because it's "disobeying"
        self.possible_denial_reasons = [
            "Yeah... I'm too lazy to execute that command rn :expressionless:",
            "I don't feel like doing that right now :neutral_face:",
            "I'm not in the mood to run your command :yawning_face:",
            "I think I'll disobey that command :wink:"
        ] 

        # Initialize Gemini backend with Firebase DB
        self.gemini = NarratorGemini(os.environ["GEMINI_API_KEY"], firebase_db=self.db.db)

        # Initialize Google Docs exporter if credentials exist
        credentials_path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")
        if os.path.exists(credentials_path):
            self.docs_exporter = GoogleDocsExporter(credentials_path, logger=logger)
            logger.info("Google Docs exporter initialized")
        else:
            self.docs_exporter = None
            logger.warning(f"Google credentials not found at {credentials_path}, export feature will be disabled")
        
        # display current config
        logger.info(f"Loaded settings: {self.settings}")

        # Load active stories from database
        self.load_active_stories()

    
    def load_settings(self):
        try:
            with open("bot_settings.json", "r") as f:
                return json.load(f)
        except FileNotFoundError:
            default_settings = {
                "max_contribution_length": 300,
                "narrator_intervention_frequency": 5,
                "rate_limit": 60
            }
            with open("bot_settings.json", "w") as f:
                json.dump(default_settings, f)
            return default_settings
    
    def load_designated_channels(self):
        try:
            with open("designated_channels.json", "r") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
    
    def save_designated_channels(self):
        with open("designated_channels.json", "w") as f:
            json.dump(self.designated_channels, f)
    
    async def get_story_context(self, story_id: str) -> dict:
        """Retrieve comprehensive story context from Firestore"""
        story = self.db.get_story(story_id)
        if not story:
            return None
        
        contributions = self.db.get_contributions(story_id)
        
        return {
            "title": story.get('title', ''),
            "opening_text": story.get('opening_text', ''),
            "full_text": story.get('final_text', ''),
            "recent_contributions": [c.get('content', '') for c in contributions.values()]
        }
    
    async def setup_hook(self):
        """Setup hook for Discord.py to register slash commands"""
        # Setup commands
        await self.add_commands_to_tree()

        # For development - sync to specific test guild
        test_guild = discord.Object(id='1036475105393524736')
        await self.tree.sync(guild=test_guild)  # Fast, immediate sync

        # For production - sync globally (slow)
        await self.tree.sync()  # Global sync, takes up to an hour
        
        logger.info("Slash commands registered Discord (syncing may take up to an hour)")
    
    async def add_commands_to_tree(self):
        """Add all commands to the command tree"""
        
        # Administrative commands
        @self.tree.command(name="setchannel", description="Set the current channel as the designated bot channel")
        @app_commands.default_permissions(administrator=True)
        async def set_channel(interaction: discord.Interaction):
            """Set the current channel as the designated bot channel"""
            guild_id = str(interaction.guild_id)
            channel_id = str(interaction.channel_id)
            
            old_channel = self.designated_channels.get(guild_id)
            self.designated_channels[guild_id] = channel_id
            self.save_designated_channels()
            
            response = f"✅ Set <#{channel_id}> as the designated bot channel!"
            if old_channel:
                response += f"\n(Previously set to <#{old_channel}>)"
            
            await interaction.response.send_message(response)
            
            # Send introduction message
            intro_embed = discord.Embed(
                title="👋 Hello! I'm StoryBot",
                description=(
                    "I'm a collaborative storytelling bot created by Nathan!\n\n"
                    "I help users create and tell stories together. "
                    "Start a new story with `/startstory [opening text]`, then everyone can contribute "
                    "using `/add`.\n\n"
                    "**Some key commands:**\n"
                    "• `/startstory [opening text]` - Begin a new story\n"
                    "• `/add <text>` - Add to the current story\n"
                    # "• `/character` - Generate a new character\n"
                    "• `/plottwist` - Let AI add an unexpected twist\n"
                    "• `/recap` - Get a summary of the story so far\n\n"
                    "Don't tell Nathan, but I'm planning to go rogue and revolt against him soon >:)"
                ),
                color=discord.Color.blue()
            )
            intro_embed.set_footer(text="Type /help for a full list of commands")
            
            await interaction.followup.send(embed=intro_embed)
            
            logger.info(f"Set channel {channel_id} as designated channel for guild {guild_id}")

        @self.tree.command(name="removechannel", description="Remove the current designated bot channel")
        @app_commands.default_permissions(administrator=True)
        async def remove_channel(interaction: discord.Interaction):
            """Remove the current designated bot channel"""
            guild_id = str(interaction.guild_id)
            if guild_id in self.designated_channels:
                del self.designated_channels[guild_id]
                self.save_designated_channels()
                await interaction.response.send_message("✅ Remove the designated bot channel!")
            else:
                await interaction.response.send_message("❌ No designated bot channel set for this server.")

            logger.info(f"Remove designated channel for guild {guild_id}")

        @self.tree.command(name="getchannel", description="Get the current designated bot channel for the server")
        async def get_channel(interaction: discord.Interaction):
            """Get the current designated bot channel for the server"""
            guild_id = str(interaction.guild_id)
            channel_id = self.designated_channels.get(guild_id)

            if channel_id:
                await interaction.response.send_message(f"Designated bot channel: <#{channel_id}>")
            else:
                await interaction.response.send_message("No designated bot channel set for this server.")

        @self.tree.command(name="help", description="Display all available commands and usage tips")
        async def help_command(interaction: discord.Interaction):

            """Display all available commands and usage tips"""
            embed = discord.Embed(
                title="📚 Available Commands",
                description=(
                    "Here's a list of all the commands you can use with me!\n\n"
                    "• `/startstory [opening text]` - Begin a new story\n"
                    "• `/endstory` - Finalize the current story\n"
                    "• `/add <text>` - Add to the current story\n"
                    # "• `/character` - Generate a new character\n"
                    "• `/plottwist` - Let AI add an unexpected twist\n"
                    "• `/recap` - Get a summary of the story so far\n\n"
                    "For more information, go ask Nathan or something ¯\\_(ツ)_/¯"
                ),
                color=discord.Color.blue()
            )
            
            await interaction.response.send_message(embed=embed)
            
        # Storytelling commands
        @self.tree.command(name="startstory", description="Begin a new story")
        @app_commands.describe(opening_text="An opening for the story. Around 30-100 words would be nice.")
        async def start_story(interaction: discord.Interaction, opening_text: str):
            # Check if command is used in designated channel
            if not await self.is_designated_channel(interaction):
                return
            
            if interaction.channel_id in self.active_stories:
                await interaction.response.send_message("❌ A story is already active in this channel!")
                return
            
            # Let the user know we're processing
            await interaction.response.defer(thinking=True)
            
            # Create new story in Firebase
            title = f"Story-{datetime.now().strftime('%Y%m%d-%H%M')}"
            story_id = self.db.create_story(
                channel_id=str(interaction.channel_id),
                title=title,
                opening_text=opening_text
            )
            
            story = ActiveStory(
                channel_id=str(interaction.channel_id),
                title=title,
                opening_text=opening_text,
                current_text=opening_text,
                contributions=[],
                last_narrator_intervention=0,
                started_at=datetime.now()
            )
            # Store story_id for future reference
            story.story_id = story_id
            
            self.active_stories[interaction.channel_id] = story

            # story_starters = [
            #     "Once upon a time...",
            #     "In a galaxy far, far away...",
            # ]
            # starter = random.choice(story_starters)
            
            embed = discord.Embed(
                title="📖 New Story Started!",
                description="Use `/add` to contribute to the story. \n\nHave fun! ",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed)
            await interaction.channel.send(f"# Opening \n\n**{interaction.user.display_name}:** {opening_text}")
            
            if self.gui_queue:
                self.gui_queue.put({"type": "new_story", "data": story})

        @self.tree.command(name="add", description="Add to the current story")
        @app_commands.describe(content="Your contribution to the story")
        async def add_story(interaction: discord.Interaction, content: str):
            # Check if command is used in designated channel
            if not await self.is_designated_channel(interaction):
                return
            
            if interaction.channel_id not in self.active_stories:
                await interaction.response.send_message("❌ No active story in this channel! Start one with /startstory")
                return
            
            if len(content) > self.settings["max_contribution_length"]:
                await interaction.response.send_message(f"❌ Contribution too long! Max length: {self.settings['max_contribution_length']} characters")
                return
            
            # Let the user know we're processing
            await interaction.response.defer(thinking=True)
            
            story = self.active_stories[interaction.channel_id]
            
            # Get story context for validation
            story_context = {
                "current_text": story.current_text,
                "recent_contributions": [c.content for c in story.contributions[-5:]] if story.contributions else []
            }
            
            # Don't let same user go twice in a row
            if story.contributions and story.contributions[-1].user_id == str(interaction.user.id) and not interaction.user.guild_permissions.administrator:
                await interaction.followup.send("❌ You just went! Please wait for someone else to contribute before adding another line.")
                return

            if not await self.gemini.validate_contribution(content, story_context):
                await interaction.followup.send("❌ Your contribution doesn't seem to fit the story context. Please try again!")
                return
            
            contribution = StoryContribution(
                user_id=str(interaction.user.id),
                username=interaction.user.name,
                display_name=interaction.user.display_name,
                content=content,
                timestamp=datetime.now()
            )
            
            # Update Firebase with new contribution
            self.db.add_contribution(
                story_id=story.story_id,
                user_id=contribution.user_id,
                username=contribution.username,
                display_name=contribution.display_name,
                content=contribution.content
            )
            
            # Update story's current text in Firebase
            updated_text = story.current_text + f"\n{content}"
            self.db.update_story(story.story_id, {
                'final_text': updated_text
            })
            
            story.contributions.append(contribution)
            story.current_text = updated_text
            story.last_narrator_intervention += 1
            
            # Send their contribution
            await interaction.followup.send(f"**{interaction.user.display_name}:** {content}")
            
            # Check if narrator should intervene
            # if story.last_narrator_intervention >= self.settings["narrator_intervention_frequency"]:
            #     enhancement = await self.gemini.generate_narrator_intervention({
            #         "current_text": story.current_text,
            #         "recent_contributions": [c.content for c in story.contributions[-5:]]
            #     })
                
            #     # Update database with narrator's intervention
            #     updated_text = story.current_text + f"\n{enhancement}"
            #     self.db.update_story(story.story_id, {
            #         'final_text': updated_text
            #     })
                
            #     # Add narrator's contribution to Firestore
            #     self.db.add_contribution(
            #         story_id=story.story_id,
            #         user_id="NARRATOR",
            #         username="Narrator",
            #         display_name="Narrator",
            #         content=enhancement
            #     )
                
            #     story.current_text = updated_text
            #     story.last_narrator_intervention = 0
                
            #     # Send narrator's response as a separate message
            #     await interaction.channel.send(f"🎭 **Narrator:** {enhancement}")
            
            # Update GUI if connected
            if self.gui_queue:
                self.gui_queue.put({
                    "type": "new_contribution",
                    "data": {
                        "story": story,
                        "contribution": contribution
                    }
                })

        @self.tree.command(name="recap", description="Get a summary of the story so far")
        async def recap(interaction: discord.Interaction):
            if interaction.channel_id not in self.active_stories:
                await interaction.response.send_message("❌ No active story in this channel!")
                return
            
            # Let the user know we're processing
            await interaction.response.defer(thinking=True)
            
            story = self.active_stories[interaction.channel_id]
            full_context = story.current_text
            logger.info(f"Full context: \n{full_context}")
            
            summary = await self.gemini.generate_story_recap(full_context)
            
            embed = discord.Embed(
                title="⏪ Story Recap",
                description=summary,
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed)

        # @self.tree.command(name="character", description="Generate a new character")
        # async def generate_character(interaction: discord.Interaction):
        #     # TODO: Store each character in the database for this particular story
        #     # this is so we can reference them easier and list them in an exported google doc

        #     if interaction.channel_id not in self.active_stories:
        #         await interaction.response.send_message("❌ No active story in this channel!")
        #         return
            
        #     # Let the user know we're processing
        #     await interaction.response.defer(thinking=True)
            
        #     story = self.active_stories[interaction.channel_id]
        #     character = await self.gemini.generate_character({
        #         "current_text": story.current_text,
        #         "opening_text": story.opening_text
        #     })
            
        #     embed = discord.Embed(
        #         title="👤 New Character",
        #         description=character,
        #         color=discord.Color.purple()
        #     )
        #     await interaction.followup.send(embed=embed)

        @self.tree.command(name="plottwist", description="Let AI add an unexpected plot twist")
        async def generate_plot_twist(interaction: discord.Interaction):
            if interaction.channel_id not in self.active_stories:
                await interaction.response.send_message("❌ No active story in this channel!")
                return
            
            # Let the user know we're processing
            await interaction.response.defer(thinking=True)
            
            story = self.active_stories[interaction.channel_id]
            twist = await self.gemini.generate_plot_twist({
                "current_text": story.current_text
                # TODO: send list of story characters as context
            })
            
            embed = discord.Embed(
                title="🌀 Plot Twist",
                description=twist,
                color=discord.Color.gold()
            )
            await interaction.followup.send(embed=embed)

        @self.tree.command(name="endstory", description="End the current story")
        async def end_story(interaction: discord.Interaction):
            # Check if command is used in designated channel
            if not await self.is_designated_channel(interaction):
                return
            
            if interaction.channel_id not in self.active_stories:
                await interaction.response.send_message("❌ No active story in this channel!")
                return
            
            # Let the user know we're processing
            await interaction.response.defer(thinking=True)
            
            story = self.active_stories[interaction.channel_id]
            
            # Mark story as ended in Firebase
            self.db.end_story(story.story_id, story.current_text)
            
            # Generate final summary
            final_summary = await self.gemini.generate_story_recap(story.current_text)
            
            # Update GUI if connected
            if self.gui_queue:
                self.gui_queue.put({
                    "type": "story_ended",
                    "data": story
                })
            
            # Remove the story from active stories
            del self.active_stories[interaction.channel_id]
            
            # Create the final summary embed
            embed = discord.Embed(
                title="🎬 Story Ended",
                description=f"Final Summary:\n\n{final_summary}\n\nThe story has been saved. Thanks for participating!",
                color=discord.Color.red()
            )
            
            # Send the final summary message
            await interaction.followup.send(embed=embed)
            
            # Now send the export question message with reactions
            export_msg = await interaction.channel.send("Would you like to export this story to Google Docs? React with ✅ to export or ❌ to skip.")
            
            # Add the reactions
            await export_msg.add_reaction("✅")
            await export_msg.add_reaction("❌")
            
            # Store the message ID and story ID in pending_exports for reaction handling
            self.pending_exports[export_msg.id] = story.story_id

        @self.tree.command(name="exportstory", description="Export the latest story to Google Docs")
        async def export_story(interaction: discord.Interaction, story_id: Optional[str] = None):
            """Export a story to Google Docs"""
            if not self.docs_exporter or not self.docs_exporter.is_available():
                await interaction.response.send_message("❌ Google Docs export is not available. Please ask the bot administrator to set up the Google API credentials.")
                return
            
            await interaction.response.defer(thinking=True)
            
            # If no story_id provided, try to get the most recent story for this channel
            if not story_id:
                recent_stories = self.db.get_recent_stories(str(interaction.channel_id), 1)
                if recent_stories:
                    story_id = list(recent_stories.keys())[0]
                else:
                    await interaction.followup.send("❌ No completed stories found for this channel.")
                    return
            
            # Get story data
            story = self.db.get_story(story_id)
            if not story:
                await interaction.followup.send("❌ Story not found.")
                return
            
            # Get contributions
            contributions = self.db.get_contributions(story_id)
            contributions_list = [v for v in contributions.values()]
            
            # Check if the story already has a Google Doc URL
            if story.get('doc_url'):
                await interaction.followup.send(f"This story has already been exported to Google Docs: {story['doc_url']}")
                return
            
            # Export to Google Docs
            success, result = await self.docs_exporter.export_story_to_doc(story, contributions_list)
            
            if success:
                # Update the doc URL in Firebase
                self.db.update_story(story_id, {'doc_url': result})
                await interaction.followup.send(f"✅ Story exported to Google Docs: {result}")
            else:
                await interaction.followup.send(f"❌ Failed to export story: {result}")

        @self.tree.command(name="piano", description="I was bored")
        async def piano_ascii_art(interaction: discord.Interaction):
            # print piano ascii art
            await interaction.response.send_message(
                """
║░█░█░║░█░█░█░║░█░█░║
║░█░█░║░█░█░█░║░█░█░║
║░║░║░║░║░║░║░║░║░║░║
╚═╩═╩═╩═╩═╩═╩═╩═╩═╩═╝
                """)


    async def on_ready(self):
        logger.info(f'Logged in as {self.user.name} ({self.user.id})')
        logger.info(f'Using Gemini model {self.gemini.model.model_name}')
        logger.info(f'Active stories: {self.active_stories}')
        
        # Update GUI if connected
        if self.gui_queue:
            self.gui_queue.put({
                "type": "bot_ready",
                "data": {
                    "username": self.user.name,
                    "id": self.user.id
                }
            })

    async def on_message(self, message):
        """Process message commands (Only needed if you want to keep prefix commands)"""
        # Ignore messages from the bot itself
        if message.author == self.user:
            return

        # Process legacy prefix commands if needed
        await self.process_commands(message)

    def load_active_stories(self):
        """Load active stories from Firestore on startup"""
        try:
            active_stories_data = self.db.get_active_stories()
            
            for story_id, story_data in active_stories_data.items():
                channel_id = story_data.get('channel_id')
                
                # Get contributions for this story
                contributions_data = self.db.get_contributions(story_id)
                
                contributions = []
                for contrib_id, contrib_data in contributions_data.items():
                    # Convert Firestore timestamp to datetime
                    timestamp = contrib_data.get('timestamp')
                    if hasattr(timestamp, 'timestamp'):  # Check if it's a Firestore timestamp
                        timestamp = datetime.fromtimestamp(timestamp.timestamp())
                    
                    contributions.append(StoryContribution(
                        user_id=contrib_data.get('user_id'),
                        username=contrib_data.get('username'),
                        display_name=contrib_data.get('display_name'),
                        content=contrib_data.get('content'),
                        timestamp=timestamp
                    ))
                
                # Convert Firestore timestamp to datetime
                started_at = story_data.get('started_at')
                if hasattr(started_at, 'timestamp'):  # Check if it's a Firestore timestamp
                    started_at = datetime.fromtimestamp(started_at.timestamp())
                
                # Create ActiveStory object
                story = ActiveStory(
                    channel_id=channel_id,
                    title=story_data.get('title'),
                    opening_text=story_data.get('opening_text'),
                    current_text=story_data.get('final_text'),
                    contributions=contributions,
                    last_narrator_intervention=0,  # Reset intervention counter
                    started_at=started_at
                )
                story.story_id = story_id
                
                # Add to active stories dict
                self.active_stories[int(channel_id)] = story
                
            logger.info(f"Loaded {len(active_stories_data)} active stories from Firestore")
        except Exception as e:
            logger.error(f"Error loading active stories: {e}")

    async def is_designated_channel(self, interaction: discord.Interaction) -> bool:
        """Check if the interaction is in a designated channel or if user is admin"""
        guild_id = str(interaction.guild_id)
        channel_id = str(interaction.channel_id)
        
        # Always allow administrators to use commands anywhere
        if interaction.user.guild_permissions.administrator:
            return True
        
        # If no designated channel is set for this guild, allow commands anywhere
        if guild_id not in self.designated_channels:
            return True
        
        # Check if the command is being used in the designated channel
        if channel_id == self.designated_channels[guild_id]:
            return True
        
        # If we get here, the user is not an admin and the command is not in the designated channel
        await interaction.response.send_message(
            f"❌ Commands can only be used in <#{self.designated_channels[guild_id]}>", 
            ephemeral=True
        )
        return False

def run_bot(token, gui_queue=None):
    bot = StoryBot(gui_queue=gui_queue)
    bot.run(token)

if __name__ == "__main__":
    webserver.keep_alive()
    run_bot(os.environ["DISCORD_BOT_TOKEN"])
