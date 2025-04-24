import queue
import threading
from narrator_bot import run_bot

class BotConnector:
    def __init__(self, token):
        self.token = token
        self.queue = queue.Queue()
        self.bot_thread = None
    
    def start(self):
        self.bot_thread = threading.Thread(
            target=run_bot,
            args=(self.token, self.queue),
            daemon=True
        )
        self.bot_thread.start()
    
    def stop(self):
        # Implement clean shutdown if needed
        pass
    
    def get_update(self):
        try:
            return self.queue.get_nowait()
        except queue.Empty:
            return None