import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
from google.cloud.firestore_v1.base_query import FieldFilter
import json
import logging
import os
from dotenv import load_dotenv
from google_docs_exporter import GoogleDocsExporter

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('firebase_db')

class FirebaseDatabase:
    def __init__(self):
        load_dotenv()

        # Parse JSON string from environment variable
        # This approach avoids the need to write the credentials to a file
        cred_dict = json.loads(os.environ.get("FIREBASE_CREDENTIALS_JSON"))
        
        try:
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            self.db = firestore.client()
            logger.info("Firestore database initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing Firestore: {e}")
            raise e
        
    def delete_existing_story(self, guild_id: str, google_docs_exporter: GoogleDocsExporter):
        """Delete existing story and its contributions"""
        stories = self.db.collection('stories')\
                    .where('guild_id', '==', str(guild_id))\
                    .stream()
        
        for story in stories: # there should only be one story... but just in case
            story_id = story.id
            story_data = story.to_dict()

            # Delete Google Docs if available
            if google_docs_exporter and google_docs_exporter.is_available():
                doc_url = story_data.get('doc_url')
                if doc_url:
                    doc_id = doc_url.split('/')[-2]  # Usually the ID is second-to-last
                    try:
                        google_docs_exporter.delete_doc(doc_id)
                    except Exception as e:
                        logger.error(f"Failed to delete Google Doc {doc_id}: {e}")
            
            # Delete contributions
            contributions = self.db.collection('contributions')\
                                .where('story_id', '==', story_id)\
                                .stream()
            for contrib in contributions:
                contrib.reference.delete()
            
            # Delete story
            story.reference.delete()
    
    # Story operations
    def create_story(self, channel_id, title, opening_text, guild_id, google_docs_exporter: GoogleDocsExporter = None):
        """Create a new story and return its ID"""
        # Enforce single active story: remove any existing stories and their contributions
        self.delete_existing_story(guild_id, google_docs_exporter)

        story_ref = self.db.collection('stories').document()
        story_id = story_ref.id
        
        story_data = {
            'channel_id': str(channel_id),
            'guild_id': str(guild_id),
            'title': title,
            'opening_text': opening_text,
            'final_text': opening_text,
            'started_at': datetime.now(),
            'ended_at': None,
            'doc_url': None,
            'contribution_count': 1,  # Start with 1 for the opening
            'isExported': False
        }
        
        story_ref.set(story_data)
        return story_id
    
    def get_active_stories(self):
        """Get all active stories (not ended)"""
        stories_ref = self.db.collection('stories').where('ended_at', '==', None).stream()
        return {doc.id: doc.to_dict() for doc in stories_ref}
    
    def get_story(self, story_id):
        """Get a story by ID"""
        return self.db.collection('stories').document(story_id).get().to_dict()
    
    def update_story(self, story_id, data):
        """Update story data"""
        self.db.collection('stories').document(story_id).update(data)
    
    def end_story(self, story_id, final_text):
        """Mark a story as ended"""
        self.db.collection('stories').document(story_id).update({
            'final_text': final_text,
            'ended_at': datetime.now()
        })
    
    # Contribution operations
    def add_contribution(self, story_id, user_id, username, display_name, content):
        """Add a contribution to a story"""
        contrib_ref = self.db.collection('contributions').document()
        contrib_id = contrib_ref.id
        
        contrib_data = {
            'story_id': story_id,
            'user_id': user_id,
            'username': username,
            'display_name': display_name,
            'content': content,
            'timestamp': datetime.now()
        }
        
        contrib_ref.set(contrib_data)
        return contrib_id
    
    def get_contributions(self, story_id):
        """Get all contributions for a story"""
        contributions = self.db.collection('contributions').where('story_id', '==', story_id).stream()
        return {doc.id: doc.to_dict() for doc in contributions}
    
    def get_recent_stories(self, channel_id, limit=5):
        """Get recent stories for a channel"""
        try:
            stories = self.db.collection('stories')\
                          .where('channel_id', '==', str(channel_id))\
                          .order_by('started_at', direction=firestore.Query.DESCENDING)\
                          .limit(limit)\
                          .stream()
            return {doc.id: doc.to_dict() for doc in stories}
        except Exception as e:
            logger.error(f"Error getting recent stories: {e}")
            # Fallback to unordered query if index doesn't exist
            try:
                stories = self.db.collection('stories')\
                              .where('channel_id', '==', str(channel_id))\
                              .limit(limit)\
                              .stream()
                return {doc.id: doc.to_dict() for doc in stories}
            except Exception as fallback_error:
                logger.error(f"Fallback query failed: {fallback_error}")
                return {}

    # Designated channel operations
    def get_designated_channels(self):
        """Get all designated channels"""
        channels_ref = self.db.collection('designated_channels').stream()
        channels = {}
        for doc in channels_ref:
            data = doc.to_dict()
            channels[doc.id] = data.get('channel_id')
        return channels

    def set_designated_channel(self, guild_id, channel_id):
        """Set a designated channel for a guild"""
        self.db.collection('designated_channels').document(guild_id).set({
            'channel_id': channel_id,
            'updated_at': datetime.now()
        })

    def remove_designated_channel(self, guild_id):
        """Remove a designated channel for a guild"""
        self.db.collection('designated_channels').document(guild_id).delete()

    def get_designated_channel(self, guild_id):
        """Get the designated channel for a guild"""
        doc = self.db.collection('designated_channels').document(guild_id).get()
        if doc.exists:
            return doc.to_dict().get('channel_id')
        return None

    def get_guild_settings(self, guild_id):
        """Get settings for a specific guild"""
        doc = self.db.collection('settings').document(guild_id).get()
        if doc.exists:
            return doc.to_dict()
        return self.get_default_settings()
    
    def get_default_settings(self):
        """Return default settings for a guild"""
        return {
            "max_contribution_length": 350,
            "designated_channel": None,
            "premium": False,
            "max_story_contributions": 75,  # Max contributions before auto-ending
            "plottwist_daily_limit": 1,      # Daily limit for plot twists
            "recap_daily_limit": 1,          # Daily limit for recaps
            "story_expiry_days": 30          # Days before stories are auto-purged
        }

    def update_guild_settings(self, guild_id, settings):
        """Update settings for a guild"""
        self.db.collection('settings').document(guild_id).set(
            settings, merge=True
        )

    def get_all_guild_settings(self):
        """Get settings for all guilds"""
        settings_ref = self.db.collection('settings').stream()
        return {doc.id: doc.to_dict() for doc in settings_ref}

    def is_premium_guild(self, guild_id):
        """Check if a guild has premium status"""
        doc = self.db.collection('premium_guilds').document(guild_id).get()
        return doc.exists

    def get_command_usage(self, guild_id, command_name, period="daily"):
        """Get usage count for a specific command in a guild"""
        today = datetime.now().strftime("%Y-%m-%d")
        guild_ref = self.db.collection('command_usage').document(str(guild_id))
        doc = guild_ref.get()
        
        if doc.exists:
            guild_data = doc.to_dict()
            command_data = guild_data.get(command_name, {})
            # If the stored date matches today, return the count
            if command_data.get('date') == today:
                return command_data.get('count', 0)
        
        return 0

    def increment_command_usage(self, guild_id, command_name):
        """Increment usage count for a specific command in a guild"""
        today = datetime.now().strftime("%Y-%m-%d")
        guild_ref = self.db.collection('command_usage').document(str(guild_id))
        
        # Use transactions to safely increment the counter
        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if doc.exists:
                guild_data = doc.to_dict()
                command_data = guild_data.get(command_name, {})
                
                # Check if we need to reset for a new day
                if command_data.get('date') != today:
                    command_data = {'count': 1, 'date': today}
                else:
                    command_data['count'] = command_data.get('count', 0) + 1
                
                # Update the command data within the guild document
                guild_data[command_name] = command_data
                transaction.set(doc_ref, guild_data)
            else:
                # Create new guild document with this command
                transaction.set(doc_ref, {
                    command_name: {'count': 1, 'date': today}
                })
        
        transaction = self.db.transaction()
        update_in_transaction(transaction, guild_ref)
        
        # Return the new count
        return self.get_command_usage(guild_id, command_name)

    def get_story_count(self, guild_id):
        """Get count of stored stories for a guild. There *should* only be one at most."""
        stories = self.db.collection('stories')\
                    .where('guild_id', '==', str(guild_id))\
                    .stream()
        return len(list(stories))

    def purge_old_stories_in_guilds(self, google_docs_exporter: GoogleDocsExporter, days_to_keep=30):
        """
        Purge old stories for all non-premium guilds
        
        Args:
            days_to_keep: Number of days to keep stories before purging
        
        Returns:
            dict: Mapping of guild_id to number of stories purged
        """
        results = {}
        
        # Get all guilds
        settings_docs = self.db.collection('settings').stream()
        for doc in settings_docs:
            guild_id = doc.id
            settings = doc.to_dict()

            # Skip premium guilds
            if settings.get('premium', False):
                continue
            
            # Get guild-specific expiry setting
            guild_days_to_keep = settings.get('story_expiry_days', days_to_keep)
            guild_cutoff = datetime.now() - timedelta(days=guild_days_to_keep)
            
            # Get stories older than cutoff date for this guild
            old_stories = self.db.collection('stories')\
                            .where(filter=FieldFilter('guild_id', '==', str(guild_id)))\
                            .where(filter=FieldFilter('ended_at', '<', guild_cutoff))\
                            .stream()
            
            # Count purged stories
            purged_count = 0
            
            # Delete each story and its contributions
            for story in old_stories:
                story_id = story.id
                story_data = story.to_dict()  # Get the story data
                
                # Delete Google Docs if available
                if google_docs_exporter and google_docs_exporter.is_available():
                    doc_url = story_data.get('doc_url')
                    if doc_url:
                        doc_id = doc_url.split('/')[-2]  # Usually the ID is second-to-last
                        try:
                            google_docs_exporter.delete_doc(doc_id)
                        except Exception as e:
                            logger.error(f"Failed to delete Google Doc {doc_id}: {e}")
                
                # Delete contributions
                contributions = self.db.collection('contributions')\
                                    .where('story_id', '==', story_id)\
                                    .stream()
                for contrib in contributions:
                    contrib.reference.delete()
                
                # Delete story
                story.reference.delete()
                purged_count += 1

            if purged_count > 0:
                results[guild_id] = purged_count
                logger.info(f"Purged {purged_count} old stories for guild {guild_id}")
        
        return results
