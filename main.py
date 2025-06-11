import os
import json
import requests
import difflib  # For fuzzy matching fallback

from flask import Flask, request, make_response
from oauth2client.service_account import ServiceAccountCredentials
import gspread

#############################################
# GLOBAL DEBUG - ensure this code is running
#############################################
print("DEBUG: main.py has started!")

app = Flask(__name__)

# 1. SLACK TOKEN
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")

# 2. GOOGLE CREDS & SHEET
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")  # The ID from your Sheet URL

# We'll create a global "worksheet" object once we authenticate
worksheet = None

def init_gspread():
    """Authenticate with Google Sheets & return a worksheet object."""
    global worksheet
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scopes=scopes)
    gc = gspread.authorize(credentials)

    # Open the sheet by ID
    sheet = gc.open_by_key(GOOGLE_SHEET_ID)
    # We'll assume the first worksheet for now
    worksheet = sheet.sheet1

# Call init_gspread() at startup
init_gspread()

@app.route('/slack/events', methods=['POST'])
def slack_events():
    # Print raw request data to debug
    print("DEBUG: Raw request received:", request.data)

    # Step 1: Check Content-Type and parse accordingly
    if request.content_type == "application/json":
        data = request.get_json()
    elif request.content_type == "application/x-www-form-urlencoded":
        data = request.form.to_dict()
    else:
        return make_response("Unsupported content type", 415)

    print("DEBUG: Parsed request data:", json.dumps(data, indent=2))

    # Step 2: Handle Slack URL verification challenge
    if "challenge" in data:
        print("DEBUG: Handling Slack verification challenge")
        return make_response(json.dumps({"challenge": data["challenge"]}), 200, {"Content-Type": "application/json"})

    # Step 3: Process Slack Commands
    command = data.get('command')
    text = data.get('text', '')
    user_id = data.get('user_id')
    channel_id = data.get('channel_id')

    print(f"DEBUG: Received Slack command={command}, text={text}, user_id={user_id}, channel_id={channel_id}")

    if command == '/mypraise':
        return handle_mypraise(user_id, text)

    elif command == '/myfeedback':
        return handle_myfeedback(user_id, text)

    elif command == '/mynotez':
        return handle_mynotez(channel_id, text)

    return make_response("Unknown command", 200)

def handle_mytest(text):
    print("DEBUG: handle_mytest called with text=", text)
    return make_response(f"You said: {text}", 200)

#######################
# /mypraise
#######################
def handle_mypraise(from_user_id, text):
    """
    1) If typed_mention is <@Uxxxx>, we parse that user ID directly (Option B).
    2) Otherwise, fallback to fuzzy approach (find_user_id_by_display_name).
    """
    print(f"DEBUG: handle_mypraise called, from_user_id={from_user_id}, text='{text}'")

    parts = text.split(" ", 2)
    if len(parts) < 3:
        usage = ("Usage: /mypraise <@UserID> Value Message\n"
                 "or: /mypraise @Name Value Message\n"
                 "Example: /mypraise @Ariel Performance Great job!")
        return make_response(usage, 200)

    typed_person = parts[0]
    praise_value = parts[1]
    praise_message = parts[2]

    from_mention = f"<@{from_user_id}>"

    # 1) Check if typed_person is an exact Slack mention like <@UABC123>
    if typed_person.startswith("<@U") and typed_person.endswith(">"):
        found_id = typed_person[2:-1]  # strip <@  and >
        to_mention = typed_person      # keep as <@UABC123> for Slack mention
        print(f"DEBUG: handle_mypraise found exact mention user_id={found_id}")
    else:
        # 2) Fallback to fuzzy approach (like your partial matching code)
        display_name = typed_person.lstrip('@')
        found_id = find_user_id_by_display_name(display_name)
        if found_id:
            to_mention = f"<@{found_id}>"
            print(f"DEBUG: Found user_id={found_id} for '{display_name}' via partial/fuzzy match")
        else:
            to_mention = typed_person
            print(f"DEBUG: No user_id found for '{display_name}'. Using fallback='{typed_person}'")

    # Store in GSheet
    store_in_sheet([
        "praise",
        from_mention,
        to_mention,
        praise_value,
        praise_message
    ])

    post_to_slack_channel(
        channel="#s44_core_team",
        text=(f"{from_mention} praised {to_mention} for *{praise_value}*:\n> {praise_message}")
    )

    return make_response(f"Praise noted and posted to #s44_core_team!", 200)

#######################
# /myfeedback
#######################
def handle_myfeedback(from_user_id, text):
    """
    Same hybrid approach: <@Uxxx> if tab-complete, else fallback to fuzzy match
    """
    print(f"DEBUG: handle_myfeedback called, from_user_id={from_user_id}, text='{text}'")

    parts = text.split(" ", 1)
    if len(parts) < 2:
        usage = "Usage: /myfeedback <@UserID> Feedback or /myfeedback @Name Feedback"
        return make_response(usage, 200)

    typed_person = parts[0]
    feedback_message = parts[1]

    from_mention = f"<@{from_user_id}>"

    # Check for exact mention
    if typed_person.startswith("<@U") and typed_person.endswith(">"):
        found_id = typed_person[2:-1]
        to_mention = typed_person
        print(f"DEBUG: handle_myfeedback found exact mention user_id={found_id}")
    else:
        display_name = typed_person.lstrip('@')
        found_id = find_user_id_by_display_name(display_name)
        if found_id:
            to_mention = f"<@{found_id}>"
            print(f"DEBUG: Found user_id={found_id} for '{display_name}' via partial/fuzzy match")
        else:
            to_mention = typed_person
            print(f"DEBUG: No user_id found for '{display_name}'. Using fallback='{typed_person}'")

    store_in_sheet([
        "feedback",
        from_mention,
        to_mention,
        "",  # no "value" for feedback
        feedback_message
    ])

    return make_response("Feedback saved (private).", 200)

#######################
# /mynotez
#######################
def handle_mynotez(channel_id, text):
    """
    We'll leave /mynotez as is (without the exact mention approach) 
    unless you also want tab-complete for note references. 
    """
    print(f"DEBUG: handle_mynotez called, channel_id={channel_id}, text='{text}'")

    parts = text.split(" ", 1)
    if len(parts) == 1 and parts[0].lower() == "get":
        return make_response("Usage: /mynotez get @Name", 200)

    if parts[0].lower() == "get":
        if len(parts) < 2:
            return make_response("Usage: /mynotez get @Name", 200)
        target = parts[1]
        return get_notes(channel_id, target)
    else:
        if len(parts) < 2:
            return make_response("Usage: /mynotez @Name Note text", 200)

        target_person = parts[0]
        note_text = parts[1]
        post_to_slack_channel(
            channel=channel_id,
            text=f"Note about {target_person}: {note_text}"
        )
        return make_response(f"Saved note about {target_person}.", 200)

def get_notes(channel_id, target):
    print(f"DEBUG: get_notes called for channel_id={channel_id}, target='{target}'")

    url = "https://slack.com/api/conversations.history"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    params = {"channel": channel_id, "limit": 200}
    resp = requests.get(url, headers=headers, params=params)
    data = resp.json()

    if not data.get("ok"):
        print("DEBUG: Slack returned error in conversations.history:", data)
        return make_response("Could not retrieve messages.", 200)

    messages = data.get("messages", [])
    matching = []
    for msg in messages:
        text = msg.get("text", "")
        if target in text:
            matching.append(text)

    if not matching:
        return make_response(f"No notes found referencing {target}.", 200)

    notes_list = "\n".join([f"- {m}" for m in matching])
    result = f"Here are notes referencing {target}:\n{notes_list}"
    return make_response(result, 200)

#######################
# Slack Helpers
#######################
def post_to_slack_channel(channel, text):
    print(f"DEBUG: post_to_slack_channel -> channel={channel}, text='{text[:50]}...'")
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Content-type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}"
    }
    payload = {
        "channel": channel,
        "text": text
    }
    requests.post(url, headers=headers, json=payload)

#######################
# Fuzzy Partial Fallback
#######################
def find_user_id_by_display_name(name):
    """
    Fuzzy best-match approach. If you'd prefer simpler partial matching, 
    you can revert to your older code. 
    """
    print(f"DEBUG: find_user_id_by_display_name called for '{name}'")

    url = "https://slack.com/api/users.list"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    resp = requests.get(url, headers=headers)
    data = resp.json()

    if not data.get("ok"):
        print("DEBUG: Slack returned an error in users.list:", data)
        return None

    members = data.get("members", [])
    print(f"DEBUG: Slack has {len(members)} members in this workspace.")

    name_lower = name.lower()

    def two_way_partial(a, b):
        return (a in b) or (b in a)

    candidates = []

    for member in members:
        user_id = member.get("id", "")
        if user_id == "USLACKBOT":
            continue
        if member.get("deleted") or member.get("is_bot"):
            continue

        profile = member.get("profile", {})
        display_nm = profile.get("display_name", "")
        real_nm   = profile.get("real_name", "")
        username  = profile.get("name", "")

        combo_str = f"{display_nm} {real_nm} {username}".lower()

        # minimal partial overlap
        if two_way_partial(name_lower, combo_str):
            candidates.append(member)

    if not candidates:
        print(f"DEBUG: No partial-match candidates found for '{name}'")
        return None

    # We'll do a fuzzy similarity score so we pick the best candidate
    best_user  = None
    best_score = 0.0

    from difflib import SequenceMatcher

    for member in candidates:
        profile   = member.get("profile", {})
        disp_nm   = profile.get("display_name", "")
        real_nm   = profile.get("real_name", "")
        username  = profile.get("name", "")
        combo_str = (f"{disp_nm} {real_nm} {username}").lower()

        ratio = SequenceMatcher(None, name_lower, combo_str).ratio()
        print(f"DEBUG: Candidate user_id={member['id']}, combo_str='{combo_str}', ratio={ratio}")

        if ratio > best_score:
            best_score = ratio
            best_user  = member

    if best_user:
        print(f"DEBUG: Best-match user for '{name}' is user_id={best_user['id']} with score={best_score}")
        return best_user["id"]

    print(f"DEBUG: No best match found after scoring for '{name}'")
    return None

#######################
# Google Sheets
#######################
def store_in_sheet(row_data):
    print("DEBUG: store_in_sheet called with row_data=", row_data)
    if worksheet is None:
        print("DEBUG: worksheet is None, can't store data.")
        return
    from datetime import datetime
    timestamp = datetime.utcnow().isoformat()
    row_data.append(timestamp)
    worksheet.append_row(row_data)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
