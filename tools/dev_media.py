"""
Developer Media and Contact Automation Tools for Vega 2.0.
YouTube focus playback, fuzzy WhatsApp contact messaging/calling, and workspace folder launching.
"""

import os
import urllib.parse
import webbrowser
import subprocess
from core.config import find_vscode, BASE
from storage.contacts import find_contact_fuzzy
from tools.registry import tools


@tools.register(
    name="play_youtube_media",
    description="Searches and plays music, tutorials, or focus lofi on YouTube.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The song, artist, video topic, or playlist to play"}
        },
        "required": ["query"]
    }
)
def play_youtube_media(query: str) -> str:
    try:
        import pywhatkit
        pywhatkit.playonyt(query)
        return f"Playing '{query}' on YouTube via pywhatkit."
    except Exception:
        # Fallback to direct browser search/play URL
        encoded = urllib.parse.quote(query)
        url = f"https://www.youtube.com/results?search_query={encoded}"
        webbrowser.open(url)
        return f"Opened YouTube search for '{query}'."


@tools.register(
    name="whatsapp_contact",
    description="Fuzzy matches a contact by name and opens WhatsApp to message or call them.",
    parameters={
        "type": "object",
        "properties": {
            "contact_name": {"type": "string", "description": "Name of the person to contact"},
            "message": {"type": "string", "description": "Optional message text to pre-fill", "default": ""}
        },
        "required": ["contact_name"]
    }
)
def whatsapp_contact(contact_name: str, message: str = "") -> str:
    contact, score = find_contact_fuzzy(contact_name)
    if not contact or not contact.get("phone"):
        return f"Could not find a phone number for contact '{contact_name}'. You can add them in Settings or Contacts CSV."

    phone = contact["phone"].replace("+", "")
    encoded_msg = urllib.parse.quote(message) if message else ""
    web_url = f"https://web.whatsapp.com/send?phone={phone}" + (f"&text={encoded_msg}" if encoded_msg else "")
    
    # Try desktop URI protocol first, then web
    try:
        uri = f"whatsapp://send?phone={phone}" + (f"&text={encoded_msg}" if encoded_msg else "")
        os.startfile(uri)
        return f"Opened WhatsApp for {contact['name']} ({contact['phone']}) [Match confidence: {int(score * 100)}%]."
    except Exception:
        webbrowser.open(web_url)
        return f"Opened WhatsApp Web for {contact['name']} ({contact['phone']})."


@tools.register(
    name="open_workspace_directory",
    description="Opens project directories (diary, documents, projects, vega root) in File Explorer or VS Code.",
    parameters={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "enum": ["diary", "documents", "projects", "vega", "chats"],
                "description": "Folder target to open"
            },
            "open_in_vscode": {
                "type": "boolean",
                "default": False,
                "description": "True to launch in VS Code, False for File Explorer"
            }
        },
        "required": ["target"]
    }
)
def open_workspace_directory(target: str, open_in_vscode: bool = False) -> str:
    mapping = {
        "diary": os.path.join(BASE, "diary"),
        "documents": os.path.join(BASE, "documents"),
        "projects": os.path.join(BASE, "projects"),
        "chats": os.path.join(BASE, "chats"),
        "vega": BASE
    }
    path = mapping.get(target.lower(), BASE)
    os.makedirs(path, exist_ok=True)

    if open_in_vscode:
        code_bin = find_vscode() or "code"
        try:
            subprocess.Popen([code_bin, path], shell=True)
            return f"Opened {target} directory in VS Code."
        except Exception as e:
            return f"Failed to open VS Code: {e}"

    os.startfile(path)
    return f"Opened {target} folder in File Explorer."

