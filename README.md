# 📖 Story Bot (Working Title)

**Story Bot** is a collaborative storytelling Discord bot powered by the Gemini API. It enables users to co-author stories, generate characters and settings, and explore creativity together in a structured, gamified format.

---

## ✨ Features

- 📖 **Collaborative Storytelling**: Users build stories one message at a time.
- ✍️ **Gemini AI Assistance**: Automatically generate story intros, plot twists, summaries, and more.
- 🧙‍♂️ **Character and Setting Generators**: Inject creativity with AI-generated elements.
- 🧵 **Story Tracking**: Full story history with options to save and export.
- 🗳️ **Plot Voting & Twists** (optional): Community-driven narrative changes.

---

## 🛠 Commands

### Core Story Commands
- `!startstory <your text>`  
  Start a new story with your opening text.

- `!add <your text>`  
  Add your part to the story. The bot appends it in order.

- `!endstory`  
  Finalizes the story. Gemini can write an epilogue to wrap it up.

---

### AI Enhancer Commands
- `!recap`  
  Summarizes recent story progress.

- `!summary`  
  Full summary of the story so far.

- `!plotwist`  
  Gemini generates and inserts a surprise twist.

---

### Utility Commands
- `!storyinfo`  
  Displays story metadata (length, contributors, genre, etc.).

- `!help`  
  Displays all available commands and usage tips.

---

## ⚙️ Technologies

- **Python**
- **discord.py** (Discord bot framework)
- **Gemini API (Free Tier)** – used for story generation and enhancement
- **SQLite** – for lightweight state persistence

---
