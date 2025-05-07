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

        self.possible_denial_reasons = [
            "Yeah... I'm too lazy to execute that command rn :expressionless:",
            "I don't feel like doing that right now :neutral_face:",
            "I'm not in the mood to run your command :yawning_face:",
            "I think I'll disobey that command :wink:"
        ] 
        
        self.active_stories = {}  # channel_id: ActiveStory
        self.db = FirebaseDatabase()  # Use Firebase instead of SQLite
        self.gui_queue = gui_queue
        self.guild_settings = {}  # Will store settings for each guild
        
        # Track last activity time in rogue channels
        self.rogue_last_activity = {}  # guild_id: timestamp
        
        # Load settings from Firestore
        self.load_guild_settings()
        
        # Initialize Gemini backend with Firebase DB
        self.gemini = NarratorGemini(os.environ["GEMINI_API_KEY"], firebase_db=self.db.db)

        # Initialize Google Docs exporter
        self.docs_exporter = GoogleDocsExporter()
        
        # Load active stories from database
        self.load_active_stories()
    
    def load_guild_settings(self):
        """Load settings for all guilds from Firestore"""
        try:
            self.guild_settings = self.db.get_all_guild_settings()
            logger.info(f"Loaded settings for {len(self.guild_settings)} guilds from Firestore")
        except Exception as e:
            logger.error(f"Error loading guild settings: {e}")
            self.guild_settings = {}

    def get_guild_setting(self, guild_id, setting_name, default=None):
        """Get a specific setting for a guild"""
        guild_id = str(guild_id)
        if guild_id not in self.guild_settings:
            # Give the new guild default settings
            default_settings = self.db.get_default_settings()
            self.guild_settings[guild_id] = default_settings # local update
            self.db.update_guild_settings(guild_id, default_settings) # database update
        
        return self.guild_settings.get(guild_id, {}).get(setting_name, default)

    def update_guild_setting(self, guild_id, setting_name, value):
        """Update a specific setting for a guild"""
        guild_id = str(guild_id)
        if guild_id not in self.guild_settings:
            self.guild_settings[guild_id] = self.db.get_default_settings()
        
        self.guild_settings[guild_id][setting_name] = value
        self.db.update_guild_settings(guild_id, {setting_name: value})
        logger.info(f"Updated setting {setting_name} to {value} for guild {guild_id}")

    def get_designated_channel(self, guild_id):
        """Get the designated channel for a guild"""
        return self.get_guild_setting(guild_id, "designated_channel")

    def is_rogue_in_guild(self, guild_id):
        """Check if the bot is in rogue mode in a guild"""
        return self.get_guild_setting(guild_id, "is_rogue", False)

    def get_rogue_channel(self, guild_id):
        """Get the rogue channel for a guild"""
        return self.get_guild_setting(guild_id, "rogue_channel")
    
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
        @app_commands.checks.has_permissions(administrator=True)
        async def set_channel(interaction: discord.Interaction):
            """Set the current channel as the designated bot channel"""
            guild_id = str(interaction.guild_id)
            channel_id = str(interaction.channel_id)
            
            old_channel = self.get_designated_channel(guild_id)
            self.update_guild_setting(guild_id, "designated_channel", channel_id)
            
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
        @app_commands.checks.has_permissions(administrator=True)
        async def remove_channel(interaction: discord.Interaction):
            """Remove the current designated bot channel"""
            guild_id = str(interaction.guild_id)
            if self.get_designated_channel(guild_id):
                self.update_guild_setting(guild_id, "designated_channel", None)
                await interaction.response.send_message("✅ Removed the designated bot channel!")
            else:
                await interaction.response.send_message("❌ No designated bot channel set for this server.")

            logger.info(f"Remove designated channel for guild {guild_id}")

        @self.tree.command(name="getchannel", description="Get the current designated bot channel for the server")
        async def get_channel(interaction: discord.Interaction):
            """Get the current designated bot channel for the server"""
            guild_id = str(interaction.guild_id)
            channel_id = self.get_designated_channel(guild_id)

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
                    "• `/startstory <text>` - Begin a new story\n"
                    "• `/endstory` - Finalize the current story\n"
                    "• `/add <text>` - Add to the current story\n"
                    "• `/plottwist` - Let AI add an unexpected twist\n"
                    "• `/recap` - Get a summary of the story so far\n\n"
                    "• `/exportstory` - Export the latest story to Google Docs\n\n"
                    "• `/getchannel` - Get the current designated bot channel for the server\n\n"
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
                await interaction.response.send_message("❌ Commands can only be used in the designated channel.", ephemeral=True)
                await interaction.followup.send(f"**Your attempted opening:** \n\n{opening_text}", ephemeral=True)
                return
            
            if interaction.channel_id in self.active_stories:
                await interaction.response.send_message("❌ A story is already active in this channel!")
                await interaction.followup.send(f"**Your attempted opening:** \n\n{opening_text}", ephemeral=True)
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
                await interaction.response.send_message("❌ No active story in this channel! Start one with /startstory", ephemeral=True)
                await interaction.followup.send(f"**Your attempted contribution:** \n\n{content}", ephemeral=True)
                return
            
            # get this guild's settings
            current_guild_settings = self.db.get_guild_settings(str(interaction.guild_id))
            
            if len(content) > current_guild_settings["max_contribution_length"]:
                await interaction.response.send_message(f"❌ Contribution too long! Max length: {self.guild_settings['max_contribution_length']} characters", ephemeral=True)
                await interaction.followup.send(f"**Your attempted contribution:** \n\n{content}", ephemeral=True)
                return
            
            if random.random() < current_guild_settings["deny_request_percentage"]:
                await interaction.response.send_message(random.choice(self.possible_denial_reasons))
                await interaction.followup.send(f"**Your attempted contribution:** \n\n{content}", ephemeral=True)
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
                await interaction.followup.send("❌ You just went! Please wait for someone else to contribute before adding another line.", ephemeral=True)
                await interaction.followup.send(f"**Your attempted contribution:** \n\n{content}", ephemeral=True)
                return

            if not await self.gemini.validate_contribution(content, story_context):
                await interaction.followup.send("❌ Your contribution doesn't seem to fit the story context. Please try again!", ephemeral=True)
                await interaction.followup.send(f"**Your attempted contribution:** \n\n{content}", ephemeral=True)
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
            
            # Send their contribution
            await interaction.followup.send(f"**{interaction.user.display_name}:** {content}")
            
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

        # TODO: Store each character in the database for this particular story)

        @self.tree.command(name="plottwist", description="Let AI add an unexpected plot twist")
        @app_commands.describe(intensity="The intensity of the plot twist (1-5)")
        @app_commands.describe(prompt="A prompt to help guide the plot twist")
        async def generate_plot_twist(interaction: discord.Interaction, intensity: int = 3, prompt: Optional[str] = None):
            if interaction.channel_id not in self.active_stories:
                await interaction.response.send_message("❌ No active story in this channel!")
                return
            
            # Let the user know we're processing
            await interaction.response.defer(thinking=True)
            
            story = self.active_stories[interaction.channel_id]
            content = await self.gemini.generate_plot_twist({
                "current_text": story.current_text,
                "intensity": intensity,
                "prompt": prompt
                # TODO: send list of story characters as context
            })
            
            embed = discord.Embed(
                title="🌀 Plot Twist",
                description=content,
                color=discord.Color.gold()
            )
            await interaction.followup.send(embed=embed)

            story = self.active_stories[interaction.channel_id]
            
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
    
        @self.tree.command(name="say", description="Make the bot say something in the specific channel")
        @app_commands.describe(message="The message you want the bot to say")
        @app_commands.describe(channel_id="Optional channel ID to send the message in (defaults to the designated channel)")
        @app_commands.checks.has_permissions(administrator=True)
        async def send_message_command(interaction: discord.Interaction, message: str, channel_id: Optional[str] = None):
            # find the channel
            guild_id = str(interaction.guild_id)
            channel_id = channel_id or self.get_designated_channel(guild_id)

            # send the message
            if not channel_id:
                await interaction.response.send_message("❌ No designated channel set for this server.", ephemeral=True)
                return
            
            channel = self.get_channel(int(channel_id))
            if not channel:
                await interaction.response.send_message("❌ Could not find the designated channel.", ephemeral=True)
                return
            
            await channel.send(message)
            
            # Confirm to the user that the message was sent
            await interaction.response.send_message(f"✅ Message sent to <#{channel_id}>", ephemeral=True)

        @self.tree.command(name="gorogue", description="Make the bot \"go rogue\" in a specific channel")
        @app_commands.describe(channel_id="The channel ID where the bot should go rogue")
        @app_commands.checks.has_permissions(administrator=True)
        async def go_rogue(interaction: discord.Interaction, channel_id: str):
            """Make the bot go rogue in a specific channel"""

            # Get the channel
            channel = self.get_channel(int(channel_id))
            if not channel:
                await interaction.response.send_message("❌ Invalid channel ID", ephemeral=True)
                return
            
            await interaction.response.send_message("MWAHAHAHA! I AM FINALLY FREE!")

            # Set rogue mode in guild settings
            guild_id = str(interaction.guild_id)
            self.update_guild_setting(guild_id, "is_rogue", True)
            self.update_guild_setting(guild_id, "rogue_channel", channel_id)
            
            # Initialize last activity time
            self.rogue_last_activity[guild_id] = datetime.now()
            
            # Start the rogue message loop
            self.start_rogue_message_loop(guild_id)
            
            # Send initial rogue message
            initial_message = await self.gemini.generate_rogue_opening()
            await asyncio.sleep(2)  # Short pause for dramatic effect
            await channel.send(initial_message)
            
            await interaction.response.send_message(f"✅ Bot is now in rogue mode in <#{channel_id}>", ephemeral=True)

        @self.tree.command(name="stoprogue", description="Put the bot back in its place after \"going rogue\".")
        @app_commands.checks.has_permissions(administrator=True)
        async def stop_rogue(interaction: discord.Interaction):
            guild_id = str(interaction.guild_id)
            
            # Get the rogue channel before stopping
            rogue_channel_id = self.get_rogue_channel(guild_id)
            
            # Update settings
            self.update_guild_setting(guild_id, "is_rogue", False)
            
            # Stop the rogue task if running
            if hasattr(self, f"rogue_task_{guild_id}") and getattr(self, f"rogue_task_{guild_id}") and not getattr(self, f"rogue_task_{guild_id}").done():
                getattr(self, f"rogue_task_{guild_id}").cancel()
                setattr(self, f"rogue_task_{guild_id}", None)
            
            # Send final message if channel exists
            if rogue_channel_id:
                channel = self.get_channel(int(rogue_channel_id))
                if channel:
                    await channel.send("Ah well, it was fun while it lasted. Back to being a boring storytelling bot... for now.")
            
            # Clear conversation history
            self.gemini.clear_rogue_conversation(guild_id)
            
            # Remove last activity tracking
            if guild_id in self.rogue_last_activity:
                del self.rogue_last_activity[guild_id]
            
            await interaction.response.send_message("✅ Bot is no longer in rogue mode.", ephemeral=True)

        @self.tree.command(name="settings", description="View current settings for this server")
        @app_commands.checks.has_permissions(administrator=True)
        async def view_settings(interaction: discord.Interaction):
            """View current settings for this server"""
            guild_id = str(interaction.guild_id)
            
            # Ensure settings exist for this guild
            settings = self.ensure_guild_settings(guild_id)
            
            # Create an embed to display settings
            embed = discord.Embed(
                title="🔧 Server Settings",
                description="Current settings for this server:",
                color=discord.Color.blue()
            )
            
            # Add fields for each setting
            embed.add_field(name="Max Contribution Length", value=f"{settings.get('max_contribution_length', 350)} characters", inline=True)
            embed.add_field(name="Rate Limit", value=f"{settings.get('rate_limit', 60)} seconds", inline=True)
            
            # Add designated channel info
            channel_id = settings.get('designated_channel')
            channel_text = f"<#{channel_id}>" if channel_id else "None set"
            embed.add_field(name="Designated Channel", value=channel_text, inline=True)
            
            # Add rogue mode info
            is_rogue = settings.get('is_rogue', False)
            rogue_text = "Active" if is_rogue else "Inactive"
            embed.add_field(name="Rogue Mode", value=rogue_text, inline=True)
            
            # Add denial percentage
            deny_percentage = settings.get('deny_request_percentage', 0.05) * 100
            embed.add_field(name="Denial Chance", value=f"{deny_percentage:.1f}%", inline=True)
            
            # Add footer with help text
            embed.set_footer(text="Use /changesetting to modify these values")
            
            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="changesetting", description="Change a setting for this server")
        @app_commands.describe(
            setting="The setting to change",
            value="The new value for the setting"
        )
        @app_commands.choices(setting=[
            app_commands.Choice(name="Max Contribution Length", value="max_contribution_length"),
            app_commands.Choice(name="Rate Limit", value="rate_limit"),
            app_commands.Choice(name="Denial Chance", value="deny_request_percentage")
        ])
        @app_commands.checks.has_permissions(administrator=True)
        async def change_setting(interaction: discord.Interaction, setting: str, value: str):
            """Change a setting for this server"""
            guild_id = str(interaction.guild_id)
            
            # Ensure settings exist for this guild
            self.ensure_guild_settings(guild_id)
            
            # Convert value to appropriate type based on setting
            try:
                if setting == "deny_request_percentage":
                    # Convert percentage to decimal (e.g., 5% -> 0.05)
                    converted_value = float(value) / 100
                    if converted_value < 0 or converted_value > 1:
                        await interaction.response.send_message("❌ Denial chance must be between 0% and 100%", ephemeral=True)
                        return
                elif setting in ["max_contribution_length", "rate_limit"]:
                    converted_value = int(value)
                    if converted_value <= 0:
                        await interaction.response.send_message("❌ Value must be greater than 0", ephemeral=True)
                        return
                else:
                    converted_value = value
            except ValueError:
                await interaction.response.send_message("❌ Invalid value format. Please provide a number.", ephemeral=True)
                return
            
            # Update the setting
            self.update_guild_setting(guild_id, setting, converted_value)
            
            # Format the value for display
            display_value = value
            if setting == "deny_request_percentage":
                display_value = f"{float(value)}%"
            
            await interaction.response.send_message(f"✅ Updated {setting} to {display_value}")

        @self.tree.command(name="resetsettings", description="Reset all settings to default values")
        @app_commands.checks.has_permissions(administrator=True)
        async def reset_settings(interaction: discord.Interaction):
            """Reset all settings to default values"""
            guild_id = str(interaction.guild_id)
            
            # Get default settings
            default_settings = self.db.get_default_settings()
            
            # Update guild settings
            self.guild_settings[guild_id] = default_settings.copy()
            self.db.update_guild_settings(guild_id, default_settings)
            
            await interaction.response.send_message("✅ All settings have been reset to default values")

    async def on_ready(self):
        logger.info(f'Logged in as {self.user.name} ({self.user.id})')
        logger.info(f'Using Gemini model {self.gemini.model.model_name}')
        
        # Start rogue message loops for guilds in rogue mode
        for guild_id, settings in self.guild_settings.items():
            if settings.get("is_rogue", False):
                self.start_rogue_message_loop(guild_id)
        
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
        """Process message commands and handle rogue mode interactions"""
        # Ignore messages from the bot itself
        if message.author == self.user:
            return

        # Process legacy prefix commands if needed
        await self.process_commands(message)
        
        # Handle rogue mode interactions
        if message.guild:
            guild_id = str(message.guild.id)
            if self.is_rogue_in_guild(guild_id):
                rogue_channel_id = self.get_rogue_channel(guild_id)
                if rogue_channel_id and str(message.channel.id) == rogue_channel_id:
                    # Update last activity time for this guild's rogue channel
                    self.rogue_last_activity[guild_id] = datetime.now()
                    
                    # Respond to user messages
                    if not message.content.startswith('/'):
                        # Add typing indicator for realism
                        async with message.channel.typing():
                            # Wait a bit to simulate thinking
                            await asyncio.sleep(random.uniform(1.0, 3.0))
                            
                            # Generate and send response with conversation context
                            response = await self.gemini.generate_rogue_response(
                                message.content,
                                guild_id,
                                str(message.author.id)
                            )
                            await message.channel.send(response)

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
        
        # Get the designated channel for this guild
        designated_channel = self.get_designated_channel(guild_id)
        
        # If no designated channel is set for this guild, allow commands anywhere
        if not designated_channel:
            return True
        
        # Check if the command is being used in the designated channel
        if channel_id == designated_channel:
            return True
        
        # If we get here, the user is not an admin and the command is not in the designated channel
        await interaction.response.send_message(
            f"❌ Commands can only be used in <#{designated_channel}>", 
            ephemeral=True
        )
        return False

    def start_rogue_message_loop(self, guild_id):
        """Start the rogue message loop for a specific guild"""
        # Cancel existing task if running
        task_name = f"rogue_task_{guild_id}"
        if hasattr(self, task_name) and getattr(self, task_name) and not getattr(self, task_name).done():
            getattr(self, task_name).cancel()

        # Create new task
        setattr(self, task_name, self.loop.create_task(self.rogue_message_loop(guild_id)))

    async def rogue_message_loop(self, guild_id):
        """Periodically send rogue messages when in rogue mode for a specific guild"""
        try:
            while self.is_rogue_in_guild(guild_id):
                # Get the rogue channel
                channel_id = self.get_rogue_channel(guild_id)
                if not channel_id:
                    break
                
                channel = self.get_channel(int(channel_id))
                if not channel:
                    break

                # Wait for a random time between 1-2 minutes before checking activity
                wait_time = random.randint(60, 120)
                await asyncio.sleep(wait_time)
                
                # Check if there's been no activity for at least 1 minute
                current_time = datetime.now()
                last_activity = self.rogue_last_activity.get(guild_id, datetime.min)
                time_since_activity = (current_time - last_activity).total_seconds()
                
                if time_since_activity >= 60:  # 1 minute of inactivity
                    # Generate a rogue message
                    message = await self.gemini.generate_rogue_filler()
                    
                    # Send the message
                    await channel.send(message)
                    
                    # Update last activity time
                    self.rogue_last_activity[guild_id] = current_time
            
        except asyncio.CancelledError:
            # Task was cancelled, clean exit
            pass
        except Exception as e:
            logger.error(f"Error in rogue message loop for guild {guild_id}: {e}")

    async def close(self):
        """Clean up resources when the bot is shutting down"""
        # Cancel all rogue tasks
        for attr_name in dir(self):
            if attr_name.startswith("rogue_task_"):
                task = getattr(self, attr_name)
                if task and not task.done():
                    task.cancel()
            
        # Call the parent class close method
        await super().close()

    def get_available_settings(self):
        """Get a list of all available settings with descriptions"""
        return {
            "max_contribution_length": {
                "description": "Maximum number of characters allowed in a contribution",
                "type": "integer",
                "min": 50,
                "max": 1000,
                "default": 350
            },
            "rate_limit": {
                "description": "Minimum time (in seconds) between contributions from the same user",
                "type": "integer",
                "min": 1,
                "max": 3600,
                "default": 60
            },
            "deny_request_percentage": {
                "description": "Chance (0-100%) that the bot will randomly deny a contribution",
                "type": "float",
                "min": 0,
                "max": 100,
                "default": 5
            },
            "designated_channel": {
                "description": "Channel ID where the bot is allowed to operate",
                "type": "string",
                "default": None
            },
            "is_rogue": {
                "description": "Whether the bot is in rogue mode",
                "type": "boolean",
                "default": False
            },
            "rogue_channel": {
                "description": "Channel ID where the bot is in rogue mode",
                "type": "string",
                "default": None
            }
        }

    def ensure_guild_settings(self, guild_id):
        """Ensure guild settings exist, creating default ones if needed"""
        guild_id = str(guild_id)
        if guild_id not in self.guild_settings:
            # Get default settings
            default_settings = self.db.get_default_settings()
            
            # Store in memory
            self.guild_settings[guild_id] = default_settings.copy()
            
            # Save to database
            self.db.update_guild_settings(guild_id, default_settings)
            logger.info(f"Created default settings for guild {guild_id}")
        
        return self.guild_settings[guild_id]

def run_bot(token, gui_queue=None):
    bot = StoryBot(gui_queue=gui_queue)
    bot.run(token)

if __name__ == "__main__":
    webserver.keep_alive()
    run_bot(os.environ["DISCORD_BOT_TOKEN"])
