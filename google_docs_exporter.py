from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import logging
from datetime import datetime
import json
import os
from dotenv import load_dotenv
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('google_docs_exporter')

class GoogleDocsExporter:
    def __init__(self):
        load_dotenv()
        self.setup_credentials()
        self.template_path = "doc_formatting_template.json"
        
    def setup_credentials(self):
        try:
            # Parse JSON string from environment variable
            # This approach avoids the need to write the credentials to a file
            cred_dict = json.loads(os.environ.get("GOOGLE_CREDENTIALS_JSON"))

            self.credentials = Credentials.from_service_account_info(cred_dict)
            self.docs_service = build('docs', 'v1', credentials=self.credentials)
            self.drive_service = build('drive', 'v3', credentials=self.credentials)
            logger.info("Google Docs API credentials loaded successfully")
        except Exception as e:
            logger.error(f"Error setting up Google Docs API: {e}")
            self.credentials = None
            
    def is_available(self):
        return self.credentials is not None
    
    def parse_and_generate_style_requests(self, text, base_offset):
        style_requests = []
        clean_text = ""
        cursor = 0
        patterns = [
            (r"\*\*(.+?)\*\*", {'bold': True}),
            (r"\*(.+?)\*", {'italic': True}),
            (r"__(.+?)__", {'underline': True})
        ]

        # Track original text index mapping
        index_shift = 0

        def replace(match, style):
            nonlocal style_requests, clean_text, index_shift
            start = len(clean_text)
            content = match.group(1)
            clean_text += content
            end = len(clean_text)
            style_requests.append({
                'updateTextStyle': {
                    'range': {
                        'startIndex': base_offset + start,
                        'endIndex': base_offset + end
                    },
                    'textStyle': style,
                    'fields': ','.join(style.keys())
                }
            })
            return ''  # remove original markup from clean text

        while cursor < len(text):
            segment = text[cursor:]
            for pattern, style in patterns:
                match = re.search(pattern, segment)
                if match:
                    pre = segment[:match.start()]
                    clean_text += pre
                    cursor += match.end()
                    segment = segment[match.end():]
                    re.sub(pattern, lambda m: replace(m, style), match.group(0), count=1)
                    break
            else:
                clean_text += segment
                break

        return clean_text, style_requests

            
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
            
            # Format timestamps for metadata
            started_at = story.get('started_at')
            if hasattr(started_at, 'timestamp'):
                started_at = datetime.fromtimestamp(started_at.timestamp()).isoformat()
                # only keep date, not time
                started_at = started_at.split('T')[0]
                
            ended_at = story.get('ended_at')
            if hasattr(ended_at, 'timestamp'):
                ended_at = datetime.fromtimestamp(ended_at.timestamp()).isoformat()
                ended_at = ended_at.split('T')[0]
                
            metadata_text = (
                f"**Started:** {started_at}\n"
                f"**Completed:** {ended_at}\n\n"
                f"**Authors:** {', '.join(set(c.get('display_name') for c in contributions))}\n\n"
            )
            
            # Add story metadata (with markdown formatting)
            clean_metadata, metadata_style_requests = self.parse_and_generate_style_requests(metadata_text, 1)
            requests.append({
                'insertText': {
                    'location': {'index': 1},
                    'text': clean_metadata
                }
            })
            requests.extend(metadata_style_requests)

            # Calculate the correct index for title insertion
            metadata_end_index = 1 + len(clean_metadata)

            # Add title
            requests.append({
                'insertText': {
                    'location': {'index': metadata_end_index},
                    'text': f"{doc_title}\n\n"
                }
            })

            # Calculate the correct index for story text insertion
            title_end_index = metadata_end_index + len(doc_title) + 2

            # Add story text
            story_text = story.get('final_text')
            clean_story_text, style_requests = self.parse_and_generate_style_requests(story_text, title_end_index)
            requests.append({
                'insertText': {
                    'location': {'index': title_end_index},
                    'text': clean_story_text
                }
            })
            requests.extend(style_requests)

            # Update the indices for styling
            metadata_start = 1
            metadata_end = metadata_start + len(clean_metadata)
            title_start = metadata_end
            title_end = title_start + len(doc_title)

            # Apply header formatting
            requests.append({
                'updateTextStyle': {
                    'range': {
                        'startIndex': title_start,
                        'endIndex': title_end
                    },
                    'textStyle': {
                        'bold': True,
                        'fontSize': {
                            'magnitude': 22,
                            'unit': 'PT'
                        }
                    },
                    'fields': 'bold,fontSize'
                }
            })

            requests.append({
                'updateParagraphStyle': {
                    'range': {
                        'startIndex': title_start,
                        'endIndex': title_end
                    },
                    'paragraphStyle': {
                        'alignment': 'CENTER'
                    },
                    'fields': 'alignment'
                }
            })

            # Apply the changes
            self.docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={'requests': requests}
            ).execute()
            
            try:
                # Make the document accessible to anyone with the link
                self.drive_service.permissions().create(
                    fileId=doc_id,
                    body={
                        'type': 'anyone',
                        'role': 'reader'
                    }
                ).execute()
            except HttpError as drive_error:
                if "accessNotConfigured" in str(drive_error):
                    return False, "Google Drive API is not enabled. Please enable it in the Google Cloud Console."
                else:
                    logger.error(f"Google Drive API error: {drive_error}")
                    return True, f"Document created but sharing failed. Access it directly: https://docs.google.com/document/d/{doc_id}/edit"
            
            # Get the document URL
            doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
            
            return True, doc_url
        
        except HttpError as error:
            error_message = str(error)
            if "accessNotConfigured" in error_message and "drive.googleapis.com" in error_message:
                logger.error(f"Google Drive API not enabled: {error}")
                return False, "Google Drive API is not enabled. Please enable it in the Google Cloud Console."
            elif "accessNotConfigured" in error_message and "docs.googleapis.com" in error_message:
                logger.error(f"Google Docs API not enabled: {error}")
                return False, "Google Docs API is not enabled. Please enable it in the Google Cloud Console."
            else:
                logger.error(f"Google Docs API error: {error}")
                return False, f"Google Docs API error: {error}"
        except Exception as e:
            logger.error(f"Error exporting to Google Docs: {e}")
            return False, f"Error: {e}"
