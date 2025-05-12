import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import json
import logging
import os
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('firebase_db')

class FirebaseDatabase:
    def __init__(self):
        load_dotenv()

        # self.db = firestore.client()

        
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
    
    # Story operations
    def create_story(self, channel_id, title, opening_text, guild_id):
        """Create a new story and return its ID"""
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
            'contribution_count': 1  # Start with 1 for the opening
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
            "rate_limit": 60,
            "max_contribution_length": 350,
            "designated_channel": None,
            "is_rogue": False,
            "rogue_channel": None,
            "deny_request_percentage": 0.1,
            "premium": False,
            # Free tier limitations
            "max_story_contributions": 100,  # Max contributions before auto-ending
            "max_stored_stories": 5,         # Max stories stored before auto-purging
            "plottwist_daily_limit": 5,      # Daily limit for plot twists
            "recap_daily_limit": 5,          # Daily limit for recaps
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
        return True # For testing
        # doc = self.db.collection('premium_guilds').document(guild_id).get()
        # return doc.exists

    def get_command_usage(self, guild_id, command_name, period="daily"):
        """Get usage count for a specific command in a guild"""
        today = datetime.now().strftime("%Y-%m-%d")
        doc = self.db.collection('command_usage').document(f"{guild_id}_{command_name}_{today}").get()
        if doc.exists:
            return doc.to_dict().get('count', 0)
        return 0

    def increment_command_usage(self, guild_id, command_name):
        """Increment usage count for a specific command in a guild"""
        today = datetime.now().strftime("%Y-%m-%d")
        doc_ref = self.db.collection('command_usage').document(f"{guild_id}_{command_name}_{today}")
        
        # Use transactions to safely increment the counter
        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if doc.exists:
                count = doc.to_dict().get('count', 0) + 1
                transaction.update(doc_ref, {'count': count})
            else:
                transaction.set(doc_ref, {'count': 1, 'date': today})
        
        transaction = self.db.transaction()
        update_in_transaction(transaction, doc_ref)
        
        # Return the new count
        return self.get_command_usage(guild_id, command_name)

    def get_story_count(self, guild_id):
        """Get count of stored stories for a guild"""
        stories = self.db.collection('stories')\
                    .where('guild_id', '==', str(guild_id))\
                    .stream()
        return len(list(stories))

    def purge_old_stories(self, guild_id, days_to_keep=30):
        """Purge stories older than specified days for non-premium guilds"""
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        
        # Get stories older than cutoff date
        old_stories = self.db.collection('stories')\
                        .where('guild_id', '==', str(guild_id))\
                        .where('ended_at', '<', cutoff_date)\
                        .stream()
        
        # Delete each story and its contributions
        for story in old_stories:
            story_id = story.id
            # Delete contributions
            contributions = self.db.collection('contributions')\
                            .where('story_id', '==', story_id)\
                            .stream()
            for contrib in contributions:
                contrib.reference.delete()
            # Delete story
            story.reference.delete()


