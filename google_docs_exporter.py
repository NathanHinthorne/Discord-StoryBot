import sqlite3
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import logging
from datetime import datetime
import json
import os
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('google_docs_exporter')

class GoogleDocsExporter:
    def __init__(self):
        load_dotenv()
        self.setup_credentials()
        
    def setup_credentials(self):
        try:
            # Parse JSON string from environment variable
            # This approach avoids the need to write the credentials to a file
            # cred_dict = json.loads(os.environ.get("GOOGLE_CREDENTIALS_JSON"))
            cred_dict = {} # temp

            self.credentials = Credentials.from_service_account_info(cred_dict)
            self.docs_service = build('docs', 'v1', credentials=self.credentials)
            self.drive_service = build('drive', 'v3', credentials=self.credentials)
            logger.info("Google Docs API credentials loaded successfully")
        except Exception as e:
            logger.error(f"Error setting up Google Docs API: {e}")
            self.credentials = None
            
    def is_available(self):
        return self.credentials is not None
            
    async def export_story_to_doc(self, story, contributions):
        """
        Export story to a Google Doc
        
        Args:
            story: Story data from Firestore
            contributions: List of contributions from Firestore
        
        Returns:
            Tuple of (success, doc_url or error_message)
        """
        if not self.is_available():
            return False, "Google Docs API credentials not available"
        
        try:
            # Create a new Google Doc
            doc_title = f"{story.get('title', 'Untitled Story')}"
            doc = self.docs_service.documents().create(body={'title': doc_title}).execute()
            doc_id = doc.get('documentId')
            
            # Format the content
            requests = []
            
            # Add title
            requests.append({
                'insertText': {
                    'location': {'index': 1},
                    'text': f"{doc_title}\n\n"
                }
            })
            
            # Format timestamps for metadata
            started_at = story.get('started_at')
            if hasattr(started_at, 'timestamp'):
                started_at = datetime.fromtimestamp(started_at.timestamp()).isoformat()
                
            ended_at = story.get('ended_at')
            if hasattr(ended_at, 'timestamp'):
                ended_at = datetime.fromtimestamp(ended_at.timestamp()).isoformat()
            
            # Add story metadata
            metadata_text = (
                f"Started: {started_at}\n"
                f"Completed: {ended_at}\n\n"
                f"Contributors: {', '.join(set(c.get('username') for c in contributions))}\n\n"
                "=== STORY ===\n\n"
            )
            
            requests.append({
                'insertText': {
                    'location': {'index': len(doc_title) + 3},  # +3 for the title and two newlines
                    'text': metadata_text
                }
            })
            
            # Add story text
            if story.get('final_text'):
                requests.append({
                    'insertText': {
                        'location': {'index': len(doc_title) + 3 + len(metadata_text)},
                        'text': story.get('final_text')
                    }
                })
            else:
                # Fallback to using contributions if final_text is empty
                contributions_text = "\n\n".join([
                    f"**{c.get('username')}**: {c.get('content')}" for c in contributions
                ])
                requests.append({
                    'insertText': {
                        'location': {'index': len(doc_title) + 3 + len(metadata_text)},
                        'text': contributions_text
                    }
                })
            
            # Apply the changes
            self.docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={'requests': requests}
            ).execute()
            
            # Make the document accessible to anyone with the link
            self.drive_service.permissions().create(
                fileId=doc_id,
                body={
                    'type': 'anyone',
                    'role': 'reader'
                }
            ).execute()
            
            # Get the document URL
            doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
            
            return True, doc_url
        
        except HttpError as error:
            logger.error(f"Google Docs API error: {error}")
            return False, f"Google Docs API error: {error}"
        except Exception as e:
            logger.error(f"Error exporting to Google Docs: {e}")
            return False, f"Error: {e}"
