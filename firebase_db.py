import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
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
    
    def setup_database(self):
        # No schema setup needed for Firestore
        pass
    
    # Story operations
    def create_story(self, channel_id, title, opening_text):
        """Create a new story and return its ID"""
        story_ref = self.db.collection('stories').document()
        story_id = story_ref.id
        
        story_data = {
            'channel_id': str(channel_id),
            'title': title,
            'opening_text': opening_text,
            'final_text': opening_text,
            'started_at': datetime.now(),
            'ended_at': None,
            'doc_url': None
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
        stories = self.db.collection('stories')\
                      .where('channel_id', '==', str(channel_id))\
                      .order_by('started_at', direction=firestore.Query.DESCENDING)\
                      .limit(limit)\
                      .stream()
        return {doc.id: doc.to_dict() for doc in stories}

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

