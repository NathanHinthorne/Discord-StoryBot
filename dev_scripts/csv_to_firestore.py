import argparse
import csv
import firebase_admin
from firebase_admin import credentials, firestore
import os
import json
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('csv_to_firestore')

def parse_value(value, data_type=None):
    """Parse string values from CSV into appropriate Python types"""
    if value == '' or value.lower() == 'null' or value.lower() == 'none':
        return None
    
    if data_type:
        if data_type == 'int':
            return int(value)
        elif data_type == 'float':
            return float(value)
        elif data_type == 'bool':
            return value.lower() in ('true', 'yes', '1', 't', 'y')
        elif data_type == 'timestamp':
            try:
                # Try different date formats
                for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y/%m/%d', '%d/%m/%Y'):
                    try:
                        return datetime.strptime(value, fmt)
                    except ValueError:
                        continue
                # If none of the formats match, raise an error
                raise ValueError(f"Could not parse timestamp: {value}")
            except Exception as e:
                logger.warning(f"Failed to parse timestamp '{value}': {e}")
                return value
        elif data_type == 'array':
            return value.split(',')
    
    # Try to infer type if not specified
    if value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    try:
        if '.' in value:
            return float(value)
        return int(value)
    except ValueError:
        return value

def import_csv_to_firestore(csv_file, collection_name, id_field=None, types_file=None):
    """
    Import data from a CSV file to Firestore
    
    Args:
        csv_file: Path to the CSV file
        collection_name: Name of the Firestore collection
        id_field: Field to use as document ID (optional)
        types_file: JSON file with field type definitions (optional)
    """
    # Initialize Firestore
    try:
        # Check if Firebase app is already initialized
        firebase_admin.get_app()
    except ValueError:
        # If not, initialize with credentials
        cred_path = os.environ.get("FIREBASE_CREDENTIALS_PATH", "firebase_credentials.json")
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    
    db = firestore.client()
    collection_ref = db.collection(collection_name)
    
    # Load field types if provided
    field_types = {}
    if types_file:
        try:
            with open(types_file, 'r') as f:
                field_types = json.load(f)
            logger.info(f"Loaded field types from {types_file}")
        except Exception as e:
            logger.error(f"Failed to load types file: {e}")
    
    # Read and import CSV data
    imported_count = 0
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            # Clean up row data and convert types
            doc_data = {}
            for key, value in row.items():
                if key:  # Skip empty keys
                    data_type = field_types.get(key)
                    doc_data[key] = parse_value(value, data_type)
            
            # Use specified field as document ID or let Firestore generate one
            if id_field and id_field in doc_data:
                doc_id = str(doc_data[id_field])
                collection_ref.document(doc_id).set(doc_data)
                logger.info(f"Imported document with ID: {doc_id}")
            else:
                collection_ref.add(doc_data)
                imported_count += 1
                if imported_count % 10 == 0:
                    logger.info(f"Imported {imported_count} documents so far...")
    
    logger.info(f"Successfully imported {imported_count} documents to collection '{collection_name}'")

def main():
    parser = argparse.ArgumentParser(description='Import CSV data to Firestore')
    parser.add_argument('csv_file', help='Path to the CSV file')
    parser.add_argument('collection', help='Firestore collection name')
    parser.add_argument('--id-field', help='Field to use as document ID')
    parser.add_argument('--types', help='JSON file with field type definitions')
    
    args = parser.parse_args()
    
    import_csv_to_firestore(args.csv_file, args.collection, args.id_field, args.types)

if __name__ == '__main__':
    main()