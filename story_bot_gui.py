import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from ttkbootstrap.scrolled import ScrolledText
from datetime import datetime
import json
from bot_connector import BotConnector

CUSTOM_ICON = "./app-icon-small.ico"

class StoryBotGUI(ttk.Window):
    def __init__(self):
        super().__init__(themename="darkly")

        # Set window icon
        self.iconbitmap(CUSTOM_ICON)

        # Set taskbar icon
        self.wm_iconbitmap(CUSTOM_ICON)
        
        # Load config
        with open("config.json") as f:
            self.config = json.load(f)
        
        # Initialize bot connector
        self.bot_connector = BotConnector(self.config["discord_token"])
        self.bot_connector.start()
        
        # Setup GUI update loop
        self.after(100, self.check_bot_updates)
        
        self.title("Story Bot Admin Interface")
        self.geometry("1200x800")
        
        # Create main container
        self.main_container = ttk.Frame(self, padding=10)
        self.main_container.pack(fill=BOTH, expand=YES)
        
        # Create left and right panes
        self.left_pane = ttk.Frame(self.main_container)
        self.right_pane = ttk.Frame(self.main_container)
        self.left_pane.pack(side=LEFT, fill=BOTH, expand=YES, padx=(0, 5))
        self.right_pane.pack(side=RIGHT, fill=BOTH, expand=YES, padx=(5, 0))
        
        self.setup_story_monitor()
        self.setup_control_panel()
        self.setup_status_bar()

        # Initialize settings from config
        self.api_key_entry.delete(0, END)
        self.api_key_entry.insert(0, self.config.get("gemini_api_key", ""))
        self.rate_limit_spinbox.set(self.config.get("rate_limit", 60))
        self.max_length_spinbox.set(self.config.get("max_contribution_length", 200))

        # Add this after other initialization code
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def on_closing(self):
        """Clean up resources before closing"""
        if self.bot_connector:
            self.bot_connector.stop()
        self.quit()

    def new_story(self):
        """Start a new story"""
        # Update status
        self.status_label.configure(text="Starting new story...", bootstyle=INFO)
        # Save current settings to config file
        self.save_settings()
        # Clear story display
        self.story_text.delete(1.0, END)
        self.contributions_tree.delete(*self.contributions_tree.get_children())

    def end_story(self):
        """End the current story"""
        # Update status
        self.status_label.configure(text="Ending story...", bootstyle=INFO)
        # Clear displays
        self.story_text.delete(1.0, END)
        self.contributions_tree.delete(*self.contributions_tree.get_children())

    def generate_recap(self):
        """Generate a recap of the current story"""
        self.status_label.configure(text="Generating recap...", bootstyle=INFO)
        #TODO Implement recap generation

    def export_to_gdoc(self):
        """Export the current story to Google Docs"""
        self.status_label.configure(text="Exporting to Google Docs...", bootstyle=INFO)
        #TODO Implement Google Docs export

    def export_to_pastebin(self):
        """Export the current story to Pastebin"""
        self.status_label.configure(text="Exporting to Pastebin...", bootstyle=INFO)
        #TODO Implement Pastebin export

    def save_settings(self):
        """Save current settings to config file and update bot"""
        self.config.update({
            "gemini_api_key": self.api_key_entry.get(),
        })
        
        # Save to config file
        with open("config.json", "w") as f:
            json.dump(self.config, f, indent=4)
        
        # The bot will now read settings from Firestore instead of bot_settings.json
        # We'll update the default settings in Firestore
        if self.bot_connector and self.bot_connector.is_connected():
            # Send command to update default settings
            self.bot_connector.send_command("update_default_settings", {
                "rate_limit": int(self.rate_limit_spinbox.get()),
                "max_contribution_length": int(self.max_length_spinbox.get()),
            })
        
        self.status_label.configure(text="Settings saved", bootstyle=SUCCESS)

    def check_bot_updates(self):
        update = self.bot_connector.get_update()
        if update:
            self.handle_bot_update(update)
        self.after(100, self.check_bot_updates)

    def handle_bot_update(self, update):
        update_type = update["type"]
        data = update["data"]
        
        if update_type == "bot_ready":
            self.status_label.configure(text=f"Connected as {data['username']}")
            self.api_status.configure(text="Bot: Online", bootstyle=SUCCESS)
        
        elif update_type == "new_story":
            self.story_text.delete(1.0, END)
            self.story_text.insert(END, data.current_text)
            self.contributions_tree.delete(*self.contributions_tree.get_children())
            self.status_label.configure(text="New story started", bootstyle=SUCCESS)
            
        elif update_type == "new_contribution":
            story = data["story"]
            contribution = data["contribution"]
            
            # Update story text
            self.story_text.delete(1.0, END)
            self.story_text.insert(END, story.current_text)
            
            # Add to contributions list
            self.contributions_tree.insert(
                "",
                0,
                values=(
                    contribution.timestamp.strftime("%H:%M:%S"),
                    contribution.username,
                    contribution.content
                )
            )
            self.status_label.configure(text="New contribution added", bootstyle=SUCCESS)
            
        elif update_type == "story_ended":
            self.story_text.delete(1.0, END)
            self.contributions_tree.delete(*self.contributions_tree.get_children())
            self.status_label.configure(text="Story ended", bootstyle=INFO)

    def setup_story_monitor(self):
        # Story Monitor Section
        monitor_frame = ttk.LabelFrame(self.left_pane, text="Active Story Monitor", padding=10)
        monitor_frame.pack(fill=BOTH, expand=YES)
        
        # Story content
        self.story_text = ScrolledText(
            monitor_frame, 
            padding=5, 
            height=20, 
            autohide=True
        )
        self.story_text.pack(fill=BOTH, expand=YES)
        
        # Contribution list
        contributions_frame = ttk.LabelFrame(monitor_frame, text="Recent Contributions", padding=5)
        contributions_frame.pack(fill=BOTH, expand=YES, pady=(10, 0))
        
        # Treeview for contributions
        columns = ("timestamp", "user", "content")
        self.contributions_tree = ttk.Treeview(
            contributions_frame,
            columns=columns,
            show="headings",
            height=8
        )
        
        # Configure columns
        self.contributions_tree.heading("timestamp", text="Time")
        self.contributions_tree.heading("user", text="User")
        self.contributions_tree.heading("content", text="Content")
        
        self.contributions_tree.column("timestamp", width=100)
        self.contributions_tree.column("user", width=150)
        self.contributions_tree.column("content", width=400)
        
        self.contributions_tree.pack(fill=BOTH, expand=YES)

    def setup_control_panel(self):
        # Control Panel Section
        control_frame = ttk.LabelFrame(self.right_pane, text="Control Panel", padding=10)
        control_frame.pack(fill=BOTH, expand=YES)
        
        # Story Controls
        story_controls = ttk.LabelFrame(control_frame, text="Story Controls", padding=5)
        story_controls.pack(fill=X, pady=(0, 10))
        
        ttk.Button(
            story_controls,
            text="New Story",
            bootstyle=SUCCESS,
            command=self.new_story
        ).pack(fill=X, pady=2)
        
        ttk.Button(
            story_controls,
            text="End Story",
            bootstyle=DANGER,
            command=self.end_story
        ).pack(fill=X, pady=2)
        
        ttk.Button(
            story_controls,
            text="Generate Recap",
            bootstyle=INFO,
            command=self.generate_recap
        ).pack(fill=X, pady=2)
        
        # API Settings
        api_frame = ttk.LabelFrame(control_frame, text="Gemini API Settings", padding=5)
        api_frame.pack(fill=X, pady=(0, 10))
        
        ttk.Label(api_frame, text="API Key:").pack(fill=X)
        self.api_key_entry = ttk.Entry(api_frame, show="•")
        self.api_key_entry.pack(fill=X, pady=(0, 5))
        
        ttk.Label(api_frame, text="Rate Limit (seconds):").pack(fill=X)
        self.rate_limit_spinbox = ttk.Spinbox(
            api_frame,
            from_=1,
            to=3600,
            increment=1
        )
        self.rate_limit_spinbox.pack(fill=X)
        self.rate_limit_spinbox.set(60)
        
        # Story Settings
        settings_frame = ttk.LabelFrame(control_frame, text="Story Settings", padding=5)
        settings_frame.pack(fill=X, pady=(0, 10))
        
        ttk.Label(settings_frame, text="Max contribution length:").pack(fill=X)
        self.max_length_spinbox = ttk.Spinbox(
            settings_frame,
            from_=50,
            to=1000,
            increment=10
        )
        self.max_length_spinbox.pack(fill=X)
        self.max_length_spinbox.set(200)
        
        # Export Options
        export_frame = ttk.LabelFrame(control_frame, text="Export Options", padding=5)
        export_frame.pack(fill=X)
        
        ttk.Button(
            export_frame,
            text="Export to Google Doc",
            bootstyle=SECONDARY,
            command=self.export_to_gdoc
        ).pack(fill=X, pady=2)
        
        ttk.Button(
            export_frame,
            text="Save to Pastebin",
            bootstyle=SECONDARY,
            command=self.export_to_pastebin
        ).pack(fill=X, pady=2)

    def setup_status_bar(self):
        # Status Bar
        status_frame = ttk.Frame(self)
        status_frame.pack(fill=X, pady=(5, 0))
        
        self.status_label = ttk.Label(
            status_frame,
            text="Ready",
            bootstyle=SUCCESS
        )
        self.status_label.pack(side=LEFT)
        
        self.api_status = ttk.Label(
            status_frame,
            text="API: Connected",
            bootstyle=SUCCESS
        )
        self.api_status.pack(side=RIGHT)

if __name__ == "__main__":
    app = StoryBotGUI()
    app.mainloop()








