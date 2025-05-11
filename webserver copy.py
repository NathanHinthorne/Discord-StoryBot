# from flask import Flask, request, jsonify
# from threading import Thread
# import firebase_admin
# from firebase_admin import credentials, firestore
# import os
# from dotenv import load_dotenv
# import re
# import json
# import logging

# app = Flask('')

# # Configure logging
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger('webserver')

# load_dotenv()

# # Parse JSON string from environment variable
# # This approach avoids the need to write the credentials to a file
# cred_dict = json.loads(os.environ.get("FIREBASE_CREDENTIALS_JSON"))

# db = None
# try:
#     cred = credentials.Certificate(cred_dict)
#     firebase_admin.initialize_app(cred)
#     db = firestore.client()
#     logger.info("Firestore database initialized successfully")
# except Exception as e:
#     logger.error(f"Error initializing Firestore: {e}")
#     raise e

# @app.route('/')
# def home():
#     return "Hello. I am alive!"

# @app.route('/bmc-webhook', methods=['POST'])
# def bmc_webhook():
#     try:
#         data = request.get_json()
#         support_message = data.get("support_message", "")
#         amount = float(data.get("amount", "0"))
#         payer_name = data.get("payer_name", "unknown")

#         # Expecting a message like "GUILD:123456789012345678"
#         match = re.search(r"GUILD:(\d{17,20})", support_message)
#         if not match:
#             return jsonify({"error": "Guild ID missing from support_message"}), 400

#         guild_id = match.group(1)

#         if amount < 5.0:
#             return jsonify({"error": "Minimum $5 required for premium"}), 400

#         # Write to Firestore
#         doc_ref = db.collection("premium_guilds").document(guild_id)
#         doc_ref.set({
#             "premium": True,
#             "granted_at": firestore.SERVER_TIMESTAMP,
#             "supporter": payer_name,
#             "method": "bmc"
#         })

#         return jsonify({"status": "Premium granted"}), 200

#     except Exception as e:
#         print("Error handling BMC webhook:", e)
#         return jsonify({"error": "Internal server error"}), 500

# def run():
#     app.run(host='0.0.0.0', port=8080)

# def keep_alive():
#     '''Starts new thread that runs a function hosting a webserver.'''
#     t = Thread(target=run)
#     t.start()