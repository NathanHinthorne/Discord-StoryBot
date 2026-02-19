import random
import discord
from discord import app_commands, SelectOption
from discord.ui import Select, View, Modal, TextInput
from discord.ext import commands
import asyncio
from datetime import datetime
import sqlite3
from dataclasses import dataclass
from typing import Optional, List
import json
import logging
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
    story_id: str
    channel_id: str
    title: str
    opening_text: str
    current_text: str
    contributions: List[StoryContribution]
    started_at: datetime

class StoryBot(commands.Bot):
    def __init__(self, command_prefix="/"):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        
        # Initialize with slash commands support
        super().__init__(
            command_prefix=command_prefix,
            intents=intents,
            help_command=None
        )

        self.db = FirebaseDatabase()  # Use Firebase instead of SQLite
        self.active_stories = {}  # channel_id: ActiveStory
        self.guild_settings = {}  # Will store settings for each guild
        
        # Load settings from Firestore
        self.load_guild_settings()
        
        # Load active stories from database
        self.load_active_stories()

        # Initialize Gemini backend with Firebase DB
        self.gemini = NarratorGemini(os.environ["GEMINI_API_KEY"], firebase_db=self.db.db)

        # Initialize Google Docs exporter
        self.docs_exporter = GoogleDocsExporter()
        
        # Schedule automatic purging
        self.purge_task = None
    
    def load_guild_settings(self):
        """Load settings for all guilds from Firestore"""
        try:
            self.guild_settings = self.db.get_all_guild_settings()
            logger.info(f"Loaded settings for {len(self.guild_settings)} guilds from Firestore")
        except Exception as e:
            logger.error(f"Error loading guild settings: {e}")
            self.guild_settings = {}

    def get_guild_setting(self, guild_id, setting_name):
        """Get a specific setting for a guild"""
        guild_id = str(guild_id)
        if guild_id not in self.guild_settings:
            logger.info(f"Creating default settings for new guild {guild_id}")
            # Give the new guild default settings
            default_settings = self.db.get_default_settings()
            self.guild_settings[guild_id] = default_settings # local update
            self.db.update_guild_settings(guild_id, default_settings) # database update
        
        return self.guild_settings.get(guild_id, {}).get(setting_name)

    def update_guild_setting(self, guild_id, setting_name, value):
        """Update a specific setting for a guild"""
        guild_id = str(guild_id)
        if guild_id not in self.guild_settings:
            self.guild_settings[guild_id] = self.db.get_default_settings()
        
        self.guild_settings[guild_id][setting_name] = value
        self.db.update_guild_settings(guild_id, {setting_name: value})
        logger.info(f"Updated setting {setting_name} to {value} for guild {guild_id}")
    
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
        test_guild = discord.Object(id='1063658763225137212')
        await self.tree.sync(guild=test_guild)  # Fast, immediate sync

        # For production - sync globally (slow)
        await self.tree.sync()  # Global sync, takes up to an hour
        
        logger.info("Slash commands registered Discord (syncing may take up to an hour)")
        
        # Start the automatic purge task
        self.purge_task = self.loop.create_task(self.automatic_story_purge())
        logger.info("Started automatic story purge task")
    
    async def add_commands_to_tree(self):
        """Add all commands to the command tree"""
        
        # Administrative commands
        @self.tree.command(name="setchannel", description="(Admin) Set the current channel as the designated bot channel")
        @app_commands.checks.has_permissions(administrator=True)
        async def set_channel(interaction: discord.Interaction):
            """Set the current channel as the designated bot channel"""
            guild_id = str(interaction.guild_id)
            channel_id = str(interaction.channel_id)
            
            old_channel = self.get_guild_setting(guild_id, "designated_channel")
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
                    "Start a new story with `/startstory <text>`, then everyone can contribute "
                    "using `/add`.\n\n"
                    "**Some key commands:**\n"
                    "• `/startstory <text>` - Begin a new story\n"
                    "• `/add [text]` - Add to the current story\n"
                    "• `/plottwist` - Let AI add an unexpected twist\n"
                    "• `/recap` - Get a summary of the story so far\n\n"
                ),
                color=discord.Color.blue()
            )
            intro_embed.set_footer(text="Type /help for a full list of commands")
            
            await interaction.followup.send(embed=intro_embed)
            
            logger.info(f"Set channel {channel_id} as designated channel for guild {guild_id}")

        @self.tree.command(name="removechannel", description="(Admin) Remove the current designated bot channel")
        @app_commands.checks.has_permissions(administrator=True)
        async def remove_channel(interaction: discord.Interaction):
            """Remove the current designated bot channel"""
            guild_id = str(interaction.guild_id)
            if self.get_guild_setting(guild_id, "designated_channel"):
                self.update_guild_setting(guild_id, "designated_channel", None)
                await interaction.response.send_message("✅ Removed the designated bot channel!")
            else:
                await interaction.response.send_message("❌ No designated bot channel set for this server.")

            logger.info(f"Remove designated channel for guild {guild_id}")

        @self.tree.command(name="setmaxlength", description="(Admin) Set the maximum length of a contribution")
        @app_commands.describe(max_length="The maximum length (in characters) of a contribution")
        @app_commands.checks.has_permissions(administrator=True)
        async def set_max_length(interaction: discord.Interaction, max_length: int):
            """Set the maximum length of a contribution"""
            if max_length <= 0 or max_length > 1000:
                await interaction.response.send_message("❌ Value must be greater than 0 and less than 1000", ephemeral=True)
                return
            
            guild_id = str(interaction.guild_id)
            self.update_guild_setting(guild_id, "max_contribution_length", max_length)
            await interaction.response.send_message(f"✅ Set maximum contribution length to {max_length} characters!")

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
                    "• `/recap` - Get a summary of the story so far\n"
                    "• `/exportstory` - Export the latest story to Google Docs\n"
                    "• `/getchannel` - Get the current designated bot channel for the server\n\n"
                    "For more information, go ask Nathan or something ¯\\_(ツ)_/¯"
                ),
                color=discord.Color.blue()
            )
            
            await interaction.response.send_message(embed=embed)
            
        # Storytelling commands
        @self.tree.command(name="startstory", description="Begin a new story")
        @app_commands.describe(opening_text="An opening for the story. Around 30-100 words would be nice.")
        @app_commands.checks.has_permissions(administrator=True)
        async def start_story(interaction: discord.Interaction, opening_text: str):
            # Check if command is used in designated channel
            if not await self.is_designated_channel(interaction):
                await interaction.response.send_message("❌ Commands can only be used in the designated channel.", ephemeral=True)
                await interaction.followup.send(f"**Your attempted opening:** \n\n{opening_text}", ephemeral=True)
                return
            
            if interaction.channel_id in self.active_stories:
                await interaction.response.send_message("❌ A story is already active in this channel! An admin must end it with `/endstory` first.")
                await interaction.followup.send(f"**Your attempted opening:** \n\n{opening_text}", ephemeral=True)
                return
            
            # Let the user know we're processing
            await interaction.response.defer(thinking=True)
            
            guild_id = str(interaction.guild_id)
            story_count = self.db.get_story_count(guild_id)
            
            if story_count == 0: # First story in the guild
                await create_new_story(interaction, guild_id, opening_text)

            else:
                # UI buttons to cancel or proceed
                view = View(timeout=300) 
                cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger)
                proceed_button = discord.ui.Button(label="Proceed", style=discord.ButtonStyle.success)
                view.add_item(cancel_button)
                view.add_item(proceed_button)

                async def cancel_button_callback(button_interaction: discord.Interaction):
                    await button_interaction.response.send_message("❌ Story creation cancelled.", ephemeral=True)
                    view.stop()
                cancel_button.callback = cancel_button_callback
                
                async def proceed_button_callback(button_interaction: discord.Interaction):
                    await button_interaction.response.send_message("✅ Proceeding with story creation...", ephemeral=True)
                    view.stop()
                    await create_new_story(interaction, guild_id, opening_text)
                proceed_button.callback = proceed_button_callback

                # Get the guild's designated channel
                settings = self.db.get_guild_settings(guild_id)
                is_exported = settings.get('isExported', False)

                if is_exported:
                    await interaction.followup.send("It's recommended to make a copy of the previous story's Google Doc before proceeding, as it will be overwritten.", view=view)
                else:
                    await interaction.followup.send("⚠️ Your last story has not been exported. To keep it, consider exporting it with `/exportstory` and making a copy before starting a new story.", view=view)

        async def create_new_story(interaction: discord.Interaction, guild_id: str, opening_text: str):
            title = "Untitled Story"
            story_id = self.db.create_story(
                channel_id=str(interaction.channel_id),
                title=title,
                opening_text=opening_text,
                guild_id=guild_id,
                google_docs_exporter=self.docs_exporter
            )
            
            story = ActiveStory(
                story_id=story_id,
                channel_id=str(interaction.channel_id),
                title=title,
                opening_text=opening_text,
                current_text=opening_text,
                contributions=[],
                started_at=datetime.now()
            )

            # remove old story from active stories if it exists
            if interaction.channel_id in self.active_stories:
                del self.active_stories[interaction.channel_id]
            
            # Store active story keyed by channel id (int)
            self.active_stories[int(interaction.channel_id)] = story

            # create new contribution for opening text
            contribution = StoryContribution(
                user_id=str(interaction.user.id),
                username=interaction.user.name,
                display_name=interaction.user.display_name,
                content=opening_text,
                timestamp=datetime.now()
            )

            # Update Firebase with new contribution
            self.db.add_contribution(
                story_id=story_id,
                user_id=contribution.user_id,
                username=contribution.username,
                display_name=contribution.display_name,
                content=contribution.content
            )
            
            embed = discord.Embed(
                title="📖 New Story Started!",
                description="Use `/add` to contribute to the story. \n\nHave fun! ",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed)
            await interaction.channel.send(f"# Opening \n\n**{interaction.user.display_name}:** {opening_text}")
            
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
            guild_id = str(interaction.guild_id)
            current_guild_settings = self.db.get_guild_settings(guild_id)
            is_premium = self.db.is_premium_guild(guild_id)
            
            if len(content) > current_guild_settings["max_contribution_length"]:
                await interaction.response.send_message(f"❌ Contribution too long! Max length: {current_guild_settings['max_contribution_length']} characters. Please shorten it and try again.", ephemeral=True)
                await interaction.followup.send(f"**Your attempted contribution:** \n\n{content}", ephemeral=True)
                return
            
            # Let the user know we're processing
            await interaction.response.defer(thinking=True)
            
            story: ActiveStory = self.active_stories[interaction.channel_id]
            
            # Check contribution count for auto-ending (free tier)
            story_data: dict = self.db.get_story(story.story_id)
            contribution_count = story_data.get('contribution_count', 0) + 1
            max_contributions = current_guild_settings.get('max_story_contributions', 100)
            
            # Update contribution count
            self.db.update_story(story.story_id, {'contribution_count': contribution_count})
            
            # Warning when approaching the limit (5 or fewer contributions remaining)
            if not is_premium and 1 <= (max_contributions - contribution_count) <= 5:
                remaining = max_contributions - contribution_count
                await interaction.followup.send(
                    f"⚠️ Warning: Only {remaining} more contribution{'s' if remaining != 1 else ''} left before this story reaches the free tier limit. "
                    "The story will automatically end when the limit is reached. "
                    "Upgrade to premium for unlimited story length!"
                )
            
            # Don't let same user go twice in a row
            if story.contributions and story.contributions[-1].user_id == str(interaction.user.id) and not interaction.user.guild_permissions.administrator:
                await interaction.followup.send("❌ You just went! Please wait for someone else to contribute before adding another line.", ephemeral=True)
                await interaction.followup.send(f"**Your attempted contribution:** \n\n{content}", ephemeral=True)
                return
            
            # create new contribution
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
            updated_text = story.current_text + f"\n\n{content}"
            self.db.update_story(story.story_id, {
                'final_text': updated_text
            })
            
            story.contributions.append(contribution)
            story.current_text = updated_text
            
            # Send their contribution
            await interaction.followup.send(f"**{interaction.user.display_name}:** {content}")

            # Auto-end story if contribution limit reached for non-premium users
            if not is_premium and contribution_count >= max_contributions:
                await self.end_story_internal(interaction.channel_id)

        @self.tree.command(name="recap", description="Get a summary of the story so far")
        async def recap(interaction: discord.Interaction):
            if interaction.channel_id not in self.active_stories:
                await interaction.response.send_message("❌ No active story in this channel!")
                return
            
            # Check usage limits for non-premium users
            guild_id = str(interaction.guild_id)
            is_premium = self.db.is_premium_guild(guild_id)
            guild_settings = self.db.get_guild_settings(guild_id)
            
            if not is_premium:
                # Check daily limit
                daily_limit = guild_settings.get('recap_daily_limit')
                current_usage = self.db.get_command_usage(guild_id, 'recap')
                
                if current_usage >= daily_limit:
                    await interaction.response.send_message(
                        f"❌ You've reached the daily limit of {daily_limit} recap(s) for free tier users. "
                        "Upgrade to premium for unlimited recaps!",
                        ephemeral=True
                    )
                    return
                
                # Increment usage counter
                self.db.increment_command_usage(guild_id, 'recap')
                logger.info(f"Recap usage for guild {guild_id}: {current_usage}")
            
            # Let the user know we're processing
            await interaction.response.defer(thinking=True)
            
            story = self.active_stories[interaction.channel_id]
            full_context = story.current_text

            logger.info(f"Context: \n{full_context}")
            
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
            
            # Check usage limits for non-premium users
            guild_id = str(interaction.guild_id)
            is_premium = self.db.is_premium_guild(guild_id)
            
            if not is_premium:
                # Check daily limit
                daily_limit = self.get_guild_setting(guild_id, "plottwist_daily_limit")
                current_usage = self.db.get_command_usage(guild_id, 'plottwist')
                
                if current_usage >= daily_limit:
                    await interaction.response.send_message(
                        f"❌ You've reached the daily limit of {daily_limit} plot twist(s) for free tier users. "
                        "Upgrade to premium for unlimited plot twists!",
                        ephemeral=True
                    )
                    return
                
                # Increment usage counter
                self.db.increment_command_usage(guild_id, 'plottwist')
                logger.info(f"Plot twist usage for guild {guild_id}: {current_usage}")
            
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
            updated_text = story.current_text + f"\n\n{content}"
            self.db.update_story(story.story_id, {
                'final_text': updated_text
            })
            
            story.contributions.append(contribution)
            story.current_text = updated_text

        @self.tree.command(name="endstory", description="(Admin) End the current story")
        @app_commands.describe(story_title="The title of the story (max 100 characters)")
        @app_commands.checks.has_permissions(administrator=True)
        async def end_story(interaction: discord.Interaction, story_title: str):
            # Check if command is used in designated channel
            if not await self.is_designated_channel(interaction):
                return
            
            if interaction.channel_id not in self.active_stories:
                await interaction.response.send_message("❌ No active story in this channel!")
                return
            
            if len(story_title) == 0:
                await interaction.response.send_message("❌ Please provide a title for the story.", ephemeral=True)
                return
            
            # Let the user know we're processing
            await interaction.response.defer(thinking=True)
            
            story = self.active_stories[interaction.channel_id]
            story_id = story.story_id

            story_title = story_title[:100]
            self.db.update_story(story_id, {'title': story_title})
            
            # Mark story as ended in Firebase
            self.db.end_story(story.story_id, story.current_text)
            
            # Generate final summary
            final_summary = await self.gemini.generate_story_recap(story.current_text)

            # Update story's final text in Firebase
            self.db.update_story(story.story_id, {
                'final_text': story.current_text + f"\n\n{final_summary}"
            })

            logger.info(f"Ended story {story.story_id} in channel {interaction.channel_id}")
            
            # Create the final summary embed
            embed = discord.Embed(
                title="🎬 Story Ended",
                description=f"Final Summary:\n\n{final_summary}\n\nThe story has been saved. Thanks for participating!",
                color=discord.Color.red()
            )
            
            # Send the final summary message
            await interaction.followup.send(embed=embed)

            # Create export options view
            export_view = View(timeout=2)  # 5 minute timeout
            export_button = discord.ui.Button(label="Export to Google Docs", style=discord.ButtonStyle.primary)
            
            async def export_button_callback(button_interaction: discord.Interaction):
                # check if user is admin
                if not button_interaction.user.guild_permissions.administrator:
                    await button_interaction.response.send_message("❌ You must be an administrator to use this command!", ephemeral=True)
                    return

                await button_interaction.response.defer(thinking=True)
                await self.export_story_by_id(button_interaction, story_id)
                
                # Remove the button after use
                export_view.remove_item(export_button)
                await button_interaction.edit_original_response(view=export_view)

            export_button.callback = export_button_callback
            export_view.add_item(export_button)

            
            # Add timeout handler
            async def on_export_timeout():
                for item in export_view.children:
                    item.disabled = True
                try:
                    await interaction.channel.send(
                        "⏱️ Export option has expired. Use `/exportstory` to export the story later.",
                        view=export_view
                    )
                except Exception as e:
                    logger.error(f"Error sending export timeout message: {e}")
            export_view.on_timeout = on_export_timeout
            
            # Send export options
            guild_id = str(interaction.guild_id)
            
            await interaction.channel.send(
                f"Would you like to export this story to Google Docs? This is a good idea if you want to keep it.", 
                view=export_view
            )

        @self.tree.command(name="exportstory", description="(Admin) Export the active story to a Google Doc")
        @app_commands.checks.has_permissions(administrator=True)
        async def export_story(interaction: discord.Interaction):
            """Export a story to Google Docs"""
            if not self.docs_exporter or not self.docs_exporter.is_available():
                await interaction.response.send_message("❌ Google Docs export is not available. Please ask the bot administrator to set up the Google API credentials.")
                return
            
            if interaction.channel_id not in self.active_stories:
                await interaction.response.send_message("❌ No active story in this channel!")
                return
            
            story = self.active_stories[interaction.channel_id]
            logger.info(f"Exporting story {story.story_id} in channel {interaction.channel_id}")
            await self.export_story_by_id(interaction, story.story_id)

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

        @self.tree.command(name="settings", description="(Admin) View current settings for this server")
        @app_commands.checks.has_permissions(administrator=True)
        async def view_settings(interaction: discord.Interaction):
            """View current settings for this server"""
            guild_id = str(interaction.guild_id)
            settings = self.db.get_guild_settings(guild_id)
            
            # Create an embed to display settings
            embed = discord.Embed(
                title="🔧 Server Settings",
                description="Current settings for this server:",
                color=discord.Color.blue()
            )
            
            # Add fields for each setting
            embed.add_field(name="Max Contribution Length", value=f"{settings.get('max_contribution_length', 350)} characters", inline=True)
            
            # Add designated channel info
            channel_id = settings.get('designated_channel')
            channel_text = f"<#{channel_id}>" if channel_id else "None set"
            embed.add_field(name="Designated Channel", value=channel_text, inline=True)
            
            # Add footer with help text
            embed.set_footer(text="Use /setmaxlength and /setchannel to modify these values")
            
            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="premium", description="Show premium status and benefits")
        async def show_premium_status(interaction: discord.Interaction):
            """Show premium status and benefits"""
            guild_id = str(interaction.guild_id)
            is_premium = self.db.is_premium_guild(guild_id)
            guild_settings = self.db.get_guild_settings(guild_id)
            
            embed = discord.Embed(
                title="✨ Premium Status",
                color=discord.Color.gold() if is_premium else discord.Color.blue()
            )
            
            status_text = "✅ ACTIVE" if is_premium else "❌ INACTIVE"
            embed.add_field(name="Status", value=status_text, inline=False)
            
            # Show current limits
            embed.add_field(
                name="Story Length",
                value=f"{'Unlimited' if is_premium else guild_settings.get('max_story_contributions')} contributions",
                inline=True
            )
            
            embed.add_field(
                name="Max Stored Stories",
                value=f"{'Unlimited' if is_premium else guild_settings.get('max_stored_stories')} stories",
                inline=True
            )
            
            embed.add_field(
                name="Plot Twists",
                value=f"{'Unlimited' if is_premium else guild_settings.get('plottwist_daily_limit')}/day",
                inline=True
            )
            
            embed.add_field(
                name="Recaps",
                value=f"{'Unlimited' if is_premium else guild_settings.get('recap_daily_limit')}/day",
                inline=True
            )
            
            await interaction.response.send_message(embed=embed)

    async def end_story_internal(self, channel_id):
        """Internal method to end a story programmatically"""
        if channel_id not in self.active_stories:
            return False
        
        story = self.active_stories[channel_id]
        
        # Mark story as ended in Firebase
        self.db.end_story(story.story_id, story.current_text)
        
        # Generate final summary
        final_summary = await self.gemini.generate_story_recap(story.current_text)

        # Update story's final text in Firebase
        self.db.update_story(story.story_id, {
            'final_text': story.current_text + f"\n\n{final_summary}"
        })

        logger.info(f"Auto-ended story {story.story_id} in channel {channel_id}")
        
        # Send message to the channel
        channel = self.get_channel(channel_id)
        if channel:
            embed = discord.Embed(
                title="🎬 Story Automatically Ended",
                description=f"**Final Summary**:\n\n{final_summary}\n\nThis story reached the maximum contribution limit. The story has been saved.",
                color=discord.Color.red()
            )
            await channel.send(embed=embed)
        
        return True

    async def on_ready(self):
        logger.info(f'Logged in as {self.user.name} ({self.user.id})')
        logger.info(f'Using Gemini model {self.gemini.model.model_name}')

    async def on_message(self, message):
        """Process message commands"""
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
                    story_id=story_id,
                    channel_id=channel_id,
                    title=story_data.get('title'),
                    opening_text=story_data.get('opening_text'),
                    current_text=story_data.get('final_text'),
                    contributions=contributions,
                    started_at=started_at
                )
                
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
        designated_channel = self.get_guild_setting(guild_id, "designated_channel")
        
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

    async def close(self):
        """Clean up resources when the bot is shutting down"""

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
            "designated_channel": {
                "description": "Channel ID where the bot is allowed to operate",
                "type": "string",
                "default": None
            },
            "premium": {
                "description": "Whether the guild has premium status",
                "type": "boolean",
                "default": False
            }
        }
        
    async def export_story_by_id(self, interaction: discord.Interaction, story_id: str):
            """Helper method to export a story by ID"""
            # Defer the interaction to allow for longer processing time
            if not interaction.response.is_done():
                await interaction.response.defer()
            
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
                msg = f"This story has already been exported to Google Docs: {story['doc_url']}"
                await interaction.followup.send(msg)
                return
            
            # Export to Google Docs
            success, result = await self.docs_exporter.export_story_to_doc(story, contributions_list)

            if success:
                # Update the doc URL in Firebase
                self.db.update_story(story_id, {'doc_url': result, 'isExported': True})
                msg = f"✅ Story exported to Google Docs: {result}\n\nIt is recommended to make a copy of the document!"
            else:
                msg = f"❌ Failed to export story: {result}"

            await interaction.followup.send(msg)

    async def rename_story_by_id(self, interaction: discord.Interaction, story_id: str, new_title: str):
        """Helper method to rename a story by ID"""
        self.db.update_story(story_id, {'title': new_title})
        msg = f"✅ Story renamed to '{new_title}'"
        if not interaction.response.is_done():
            await interaction.response.send_message(msg)
        else:
            await interaction.followup.send(msg)
        return True
        
    async def automatic_story_purge(self):
        """Task to automatically purge old stories for free-tier guilds"""
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                # Run purge operation
                results = self.db.purge_old_stories_in_guilds(self.docs_exporter)
                
                # Log results
                total_purged = sum(results.values())
                if total_purged > 0:
                    logger.info(f"Automatic purge: Removed {total_purged} old stories from {len(results)} guilds")
                
                # Send notifications to guild admins if configured
                for guild_id, purged_count in results.items():
                    if purged_count > 0:
                        await self.notify_guild_about_purge(guild_id, purged_count)
                
            except Exception as e:
                logger.error(f"Error in automatic story purge: {e}")
            
            # Run once per day (86400 seconds)
            await asyncio.sleep(86400)
    
    async def notify_guild_about_purge(self, guild_id, purged_count):
        """Notify guild admins about purged stories"""
        try:
            # Get the guild's designated channel
            settings = self.db.get_guild_settings(guild_id)
            channel_id = settings.get('designated_channel')
            
            if not channel_id:
                return
            
            channel = self.get_channel(int(channel_id))
            if not channel:
                return
            
            # Send notification
            await channel.send(
                f"⚠️ **Automatic Maintenance:** {purged_count} old stories have been removed due to the free tier storage limit. "
                f"Upgrade to premium for longer storage."
            )
        except Exception as e:
            logger.error(f"Error notifying guild {guild_id} about purge: {e}")

def run_bot(token):
    bot = StoryBot()
    bot.run(token)

if __name__ == "__main__":
    run_bot(os.environ["DISCORD_BOT_TOKEN"])
